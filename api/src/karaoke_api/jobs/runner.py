import asyncio
import logging
import shutil
from pathlib import Path

from ..separation.base import StemSeparator
from ..storage.base import Storage
from .models import Stage
from .store import JobStore

log = logging.getLogger(__name__)


class JobRunner:
    """Забирает задачи из очереди и прогоняет их через разделитель.

    Работает в одном экземпляре: GPU один, параллелить нечего.
    """

    def __init__(self, store: JobStore, storage: Storage,
                 separator: StemSeparator, work_dir: Path) -> None:
        self._store = store
        self._storage = storage
        self._separator = separator
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._stopped = False

    def run_once(self) -> bool:
        """Обработать одну задачу. False — очередь пуста."""
        job = self._store.claim_next()
        if job is None:
            return False

        scratch = self._work_dir / job.id
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            track = self._store.get_track(job.track_id)
            source = self._storage.materialize(track.storage_key, scratch)

            def on_progress(stage: str, fraction: float) -> None:
                self._store.set_stage(job.id, Stage(stage), fraction)

            result = self._separator.separate(source, scratch, on_progress)

            stems = {}
            for name, path in (("vocals", result.vocals),
                               ("no_vocals", result.no_vocals)):
                key = f"tracks/{job.track_id}/stems/{name}.wav"
                self._storage.store_file(key, path)
                stems[name] = key

            self._store.finish(job.id, {"stems": stems})
        except Exception as exc:
            log.exception("задача %s упала", job.id)
            self._store.fail(job.id, f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        return True

    def stop(self) -> None:
        self._stopped = True

    async def run_forever(self, poll_interval: float = 0.5) -> None:
        """Цикл опроса. Обработка идёт в пуле потоков, чтобы не блокировать
        событийный цикл FastAPI на десятки секунд."""
        while not self._stopped:
            did_work = await asyncio.to_thread(self.run_once)
            if not did_work:
                await asyncio.sleep(poll_interval)
