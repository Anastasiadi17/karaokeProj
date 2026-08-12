import asyncio
import contextlib
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .audio.probe import UnsupportedAudio, probe_audio
from .config import Settings, get_settings
from .deps import AppState
from .jobs.store import new_id
from .ranges import parse_range

log = logging.getLogger(__name__)


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState.build(settings)
        app.state.karaoke = state

        orphans = state.store.fail_orphans()
        if orphans:
            log.warning("помечено упавшими незавершённых задач: %d", orphans)

        task = asyncio.create_task(state.runner.run_forever())
        try:
            yield
        finally:
            state.runner.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            state.store.close()

    app = FastAPI(title="Karaoke API", lifespan=lifespan)

    @app.post("/api/tracks", status_code=201)
    async def upload_track(request: Request, file: UploadFile):
        state: AppState = request.app.state.karaoke
        limits = state.settings

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "upload"
            size = 0
            with staged.open("wb") as out:
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

            if info.format not in limits.allowed_formats:
                return _error("unsupported_format")

            # Идентификатор выдаётся до вставки: ключ строится из него, и
            # запись попадает в базу сразу целиком.
            track_id = new_id()
            key = f"tracks/{track_id}/original.{info.format}"
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

    return app


app = create_app()
