import asyncio
import contextlib
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .audio.probe import UnsupportedAudio, normalize_format, probe_audio
from .cleanup import purge_expired, purge_orphan_track_dirs
from .config import Settings, get_settings
from .deps import AppState
from .gpu import check_gpu
from .jobs.store import new_id
from .ranges import parse_range

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

        state = AppState.build(settings, gpu)
        app.state.karaoke = state

        orphans = state.store.fail_orphans()
        if orphans:
            log.warning("помечено упавшими незавершённых задач: %d", orphans)

        purge_expired(state.store, state.storage, settings.file_ttl_hours)
        purge_orphan_track_dirs(state.store, state.storage)

        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                try:
                    await asyncio.to_thread(
                        purge_expired, state.store, state.storage,
                        settings.file_ttl_hours,
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
                track_id, file.filename or "upload", key, info.duration_sec
            )

        job_id = state.store.create_job(track_id)
        return {"track_id": track_id, "job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        state: AppState = request.app.state.karaoke
        job = state.store.get_job(job_id)
        if job is None:
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
        if state.store.get_track(track_id) is None:
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
        if state.store.get_track(track_id) is None:
            return _error("not_found", status=404)
        state.storage.delete_prefix(f"tracks/{track_id}")
        state.store.delete_track(track_id)
        return Response(status_code=204)

    @app.get("/api/health")
    async def health(request: Request):
        gpu = request.app.state.gpu
        return {
            "gpu": {
                "available": gpu.available,
                "device_name": gpu.device_name,
                "reason": gpu.reason,
                "hint": gpu.hint,
            },
            "separator": settings.separator,
        }

    return app


app = create_app()
