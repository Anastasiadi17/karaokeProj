import asyncio
import contextlib
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .accounts.routes import build_router as build_accounts_router
from .accounts.routes import current_user, month_start
from .audio.probe import UnsupportedAudio, normalize_format, probe_audio
from .cleanup import purge_expired, purge_orphan_track_dirs, purge_track
from .config import Settings, get_settings
from .deps import AppState
from .gpu import check_gpu
from .jobs.store import new_id
from .logging_setup import configure_logging
from .ranges import parse_range
from .track_lock import TrackLockBusy

log = logging.getLogger(__name__)


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


class RejectOversizedBody:
    """Отвергает запрос по объявленному Content-Length до разбора формы.

    Счётчик байт внутри обработчика загрузки не ограничивает ничего:
    `file: UploadFile` разрешается как зависимость уже ПОСЛЕ того, как
    FastAPI вызвал `await request.form()` (routing.py: form читается на
    строке 430, зависимости решаются на 481), а Starlette к этому моменту
    вычитал всё тело в SpooledTemporaryFile. `MultiPartParser.max_part_size`
    (1 МБ) применяется только в ветке `if self._current_part.file is None`,
    то есть к обычным полям формы — файловые части пишутся без лимита.
    Неаутентифицированный клиент таким образом кладёт на диск системного
    temp тело любого размера, а лимит срабатывает постфактум.

    Отсюда и ASGI-уровень: это единственная точка отказа ДО разбора формы.
    Заголовок можно подделать, поэтому счётчик в обработчике остаётся
    страховкой, а отсутствующий Content-Length (chunked) загрузку не
    ломает — там работает тот же счётчик.

    Настоящий периметр (nginx/Cloudflare `client_max_body_size`) это не
    заменяет: до приложения запрос всё равно доходит.
    """

    # Multipart-конверт (граница, заголовки части) добавляет к телу сотни
    # байт. Без допуска файл ровно в max_upload_bytes отвергался бы.
    ENVELOPE_SLACK = 8192

    def __init__(self, app, max_upload_bytes: int) -> None:
        self.app = app
        self._limit = max_upload_bytes + self.ENVELOPE_SLACK

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and self._declared_over_limit(scope):
            response = _error("too_large", status=413)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _declared_over_limit(self, scope) -> bool:
        for name, value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            try:
                return int(value) > self._limit
            except ValueError:
                # Мусор в заголовке — не наше дело его валидировать,
                # дальше по стеку тело всё равно посчитает счётчик.
                return False
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_json, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Проверка идёт до сборки состояния: её вердикт выбирает устройство
        # разделителя. Раньше сепаратор строился первым и решал сам по
        # torch.cuda.is_available() — то есть уходил на cuda даже тогда,
        # когда зонд уже установил, что ядер под эту архитектуру нет.
        gpu = check_gpu()
        app.state.gpu = gpu
        if gpu.available:
            log.info("GPU готов: %s", gpu.device_name)
        else:
            log.warning("GPU недоступен (%s). Обработка пойдёт на CPU и будет "
                        "в десятки раз медленнее. %s", gpu.reason, gpu.hint)

        if settings.expose_login_link:
            log.warning(
                "KARAOKE_EXPOSE_LOGIN_LINK включён: ссылка входа отдаётся в "
                "ответе API. Это режим разработки — в бою вход получит любой, "
                "кто знает чужой адрес."
            )

        state = AppState.build(settings, gpu)
        app.state.karaoke = state

        orphans = state.store.fail_orphans()
        if orphans:
            log.warning("помечено упавшими незавершённых задач: %d", orphans)

        purge_expired(state.store, state.storage, state.track_lock,
                      settings.file_ttl_hours)
        purge_orphan_track_dirs(state.store, state.storage)

        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                try:
                    await asyncio.to_thread(
                        purge_expired, state.store, state.storage,
                        state.track_lock, settings.file_ttl_hours,
                    )
                except Exception:
                    log.exception("сбой автоочистки")

        cleanup_task = asyncio.create_task(_cleanup_loop())

        task = asyncio.create_task(state.runner.run_forever())
        try:
            yield
        finally:
            state.runner.stop()
            task.cancel()
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            # Отмена сняла лишь ожидание to_thread — рабочий поток с
            # текущей задачей продолжает считать. Закрыть базу под ним
            # значит уронить его на set_stage/finish и потерять задачу
            # в состоянии running навсегда.
            finished = await asyncio.to_thread(
                state.runner.wait_until_idle, settings.shutdown_wait_sec
            )
            if not finished:
                log.warning(
                    "задача не досчитала за %s с — закрываю базу, её "
                    "результат будет потерян", settings.shutdown_wait_sec,
                )
            state.store.close()

    app = FastAPI(title="Karaoke API", lifespan=lifespan)
    app.add_middleware(
        RejectOversizedBody, max_upload_bytes=settings.max_upload_bytes
    )

    @app.post("/api/tracks", status_code=201)
    async def upload_track(request: Request, file: UploadFile):
        state: AppState = request.app.state.karaoke
        limits = state.settings

        user = current_user(request)
        if user is None:
            return _error("unauthorized", status=401)

        used = state.accounts.count_operations(user.id, month_start())
        if used >= limits.free_monthly_operations:
            return _error("quota_exceeded", status=429)

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "upload"
            size = 0
            with staged.open("wb") as out:
                # Страховка под RejectOversizedBody: заголовку Content-Length
                # верить нельзя, а при chunked его нет вовсе.
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limits.max_upload_bytes:
                        return _error("too_large")
                    out.write(chunk)

            try:
                info = probe_audio(staged)
            except UnsupportedAudio:
                return _error("unsupported_format")

            if info.duration_sec > limits.max_duration_sec:
                return _error("too_long")

            # info.format — имя контейнера от libsndfile, allowed_formats —
            # список пользовательских форматов. Для семейства wav это разные
            # словари, их надо свести, иначе обычный WAVEX-файл от редактора
            # получает «формат не поддерживается».
            fmt = normalize_format(info.format)
            if fmt not in limits.allowed_formats:
                return _error("unsupported_format")

            # Идентификатор выдаётся до вставки: ключ строится из него, и
            # запись попадает в базу сразу целиком.
            track_id = new_id()
            key = f"tracks/{track_id}/original.{fmt}"
            state.storage.store_file(key, staged)
            state.store.create_track(
                track_id, file.filename or "upload", key, info.duration_sec,
                user_id=user.id,
            )

        job_id = state.store.create_job(track_id)
        # Операция засчитывается последней: отказ по формату, длине или
        # размеру не должен съедать одну из трёх бесплатных.
        state.accounts.record_operation(user.id, "separate", track_id)
        state.accounts.record_event("track_uploaded", user.id)
        return {"track_id": track_id, "job_id": job_id}

    def _owns(request: Request, track_id: str) -> bool:
        """Чужой трек для клиента не существует.

        Отвечать 403 значило бы подтверждать, что такой идентификатор есть у
        кого-то другого, — а это ровно то, чего знать не надо.
        """
        state: AppState = request.app.state.karaoke
        track = state.store.get_track(track_id)
        if track is None:
            return False
        if track.user_id is None:
            # Загружен до аккаунтов: остаётся доступным, пока не истечёт срок
            # хранения. Осознанная уступка, а не недосмотр.
            return True
        user = current_user(request)
        return user is not None and user.id == track.user_id

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        state: AppState = request.app.state.karaoke
        job = state.store.get_job(job_id)
        if job is None:
            return _error("not_found", status=404)
        if not _owns(request, job.track_id):
            return _error("not_found", status=404)
        return {
            "status": job.status.value,
            "stage": job.stage.value if job.stage else None,
            "progress": job.progress,
            "error": job.error_message,
            "result": job.result,
        }

    _STEM_KINDS = ("vocals", "no_vocals")

    @app.get("/api/tracks/{track_id}/stems/{kind}")
    async def get_stem(request: Request, track_id: str, kind: str):
        state: AppState = request.app.state.karaoke
        if kind not in _STEM_KINDS:
            return _error("not_found", status=404)

        # Хранилище — не источник истины о том, что трек существует. Файлы
        # могут пережить удаление строки (гонка с работающей задачей, сбой
        # уборки), и отдавать их после этого нельзя: для клиента трек удалён.
        if not _owns(request, track_id):
            return _error("not_found", status=404)

        key = f"tracks/{track_id}/stems/{kind}.wav"
        if not state.storage.exists(key):
            return _error("not_found", status=404)

        total = state.storage.size(key)
        try:
            rng = parse_range(request.headers.get("range"), total)
        except ValueError:
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{total}"}
            )

        start, end = rng if rng else (0, total - 1)
        length = end - start + 1

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        if rng is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"

        # Дорожка весит десятки мегабайт: отдаём потоком, а не одним куском
        # в памяти на каждый запрос.
        return StreamingResponse(
            state.storage.iter_range(key, start, length),
            status_code=206 if rng else 200,
            media_type="audio/wav",
            headers=headers,
        )

    @app.delete("/api/tracks/{track_id}", status_code=204)
    async def delete_track(request: Request, track_id: str):
        state: AppState = request.app.state.karaoke
        if not _owns(request, track_id):
            return _error("not_found", status=404)
        try:
            # Целиком в отдельный поток: ожидание замка не должно вставать
            # поперёк событийного цикла.
            await asyncio.to_thread(
                purge_track, state.store, state.storage, state.track_lock,
                track_id, state.settings.track_lock_timeout_sec,
            )
        except TrackLockBusy:
            log.warning("удаление трека %s не дождалось замка за %s с",
                        track_id, state.settings.track_lock_timeout_sec)
            return _error("delete_failed", status=503)
        except Exception:
            # На Windows файл, занятый работающей задачей, не удаляется
            # (WinError 32) — штатный сценарий, а не сбой сервиса, и отвечать
            # на него голым 500 без кода нечестно. Та же защита, что в цикле
            # автоочистки. Строку трека purge_track в этом случае оставляет
            # намеренно: без неё файлы стали бы сиротами, а так их подберёт
            # уборка по TTL.
            log.exception("не удалось удалить файлы трека %s", track_id)
            return _error("delete_failed", status=503)
        return Response(status_code=204)

    @app.get("/api/health")
    async def health(request: Request):
        gpu = request.app.state.gpu
        # Состояние прогрева живёт у раннера, а не в app.state рядом с gpu:
        # gpu пишется один раз до старта и больше не меняется, а прогрев
        # пишет рабочий поток раннера уже после.
        warmup = request.app.state.karaoke.runner.warmup_status
        return {
            "gpu": {
                "available": gpu.available,
                "device_name": gpu.device_name,
                "reason": gpu.reason,
                "hint": gpu.hint,
            },
            "separator": settings.separator,
            "model": {
                "state": warmup.state,
                "detail": warmup.detail,
                "elapsed_sec": warmup.elapsed_sec,
            },
        }

    app.include_router(build_accounts_router(settings))
    _mount_web(app, settings)
    return app


def _mount_web(app: FastAPI, settings: Settings) -> None:
    """Раздаёт собранный фронтенд в корне, если он есть.

    Монтируется последним намеренно: Starlette проверяет маршруты по порядку,
    и `Mount("/")` съел бы всё, что зарегистрировано после него, — включая
    `/api/*`.

    Клиентских маршрутов у студии нет (экраны переключаются состоянием, не
    адресом), поэтому фолбэк «любой путь → index.html» не нужен: неизвестный
    адрес честно отвечает 404. Появятся маршруты — понадобится и фолбэк.
    """
    dist = settings.web_dist
    if dist is None:
        return

    if not dist.is_dir():
        # Не падаем: API полезен и без фронта, а «соберите web» — это
        # сообщение человеку, а не повод не подняться.
        log.warning(
            "KARAOKE_WEB_DIST указывает на %s, но такого каталога нет — "
            "фронтенд не раздаётся; соберите его командой npm run build",
            dist,
        )
        return

    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    log.info("фронтенд раздаётся из %s", dist)


app = create_app()
