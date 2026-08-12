import asyncio
import logging
import shutil
import threading
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
        # Взведено, пока рабочий поток ничего не считает. Нужно при
        # выключении: см. wait_until_idle.
        self._idle = threading.Event()
        self._idle.set()

    def run_once(self) -> bool:
        """Обработать одну задачу. False — очередь пуста."""
        self._idle.clear()
        try:
            return self._run_claimed()
        finally:
            self._idle.set()

    def wait_until_idle(self, timeout: float) -> bool:
        """Дождаться, пока текущая задача досчитает. False — не дождались.

        Обязательна перед закрытием базы при выключении. task.cancel() на
        run_forever снимает только ожидание asyncio.to_thread — сам рабочий
        поток продолжает считать. Если закрыть под ним соединение, он упадёт
        на первом же set_stage/finish, а исключение уйдёт в брошенный future
        и потеряется молча, оставив задачу навсегда в running.
        """
        return self._idle.wait(timeout)

    def _run_claimed(self) -> bool:
        job = self._store.claim_next()
        if job is None:
            return False

        scratch = self._work_dir / job.id
        try:
            scratch.mkdir(parents=True, exist_ok=True)
            track = self._store.get_track(job.track_id)
            source = self._storage.materialize(track.storage_key, scratch)

            def on_progress(stage: str, fraction: float) -> None:
                self._store.set_stage(job.id, Stage(stage), fraction)

            result = self._separator.separate(source, scratch, on_progress)

            if self._store.get_track(job.track_id) is None:
                # Трек удалили, пока задача считалась (DELETE или автоочистка
                # по TTL). Записать стемы сейчас — значит заново создать
                # каталог трека, которого нет в базе: его не увидит ни
                # list_expired_tracks, ни DELETE, и файлы останутся навсегда.
                # Строки задачи тоже уже нет, помечать нечего.
                log.info("трек %s удалён во время обработки, стемы не пишем",
                         job.track_id)
                return True

            stems = {}
            for name, path in (("vocals", result.vocals),
                               ("no_vocals", result.no_vocals)):
                key = f"tracks/{job.track_id}/stems/{name}.wav"
                self._storage.store_file(key, path)
                stems[name] = key

            self._store.finish(
                job.id, {"stems": stems, "degraded": result.degraded}
            )
        except Exception as exc:
            log.exception("задача %s упала", job.id)
            try:
                self._store.fail(job.id, f"{type(exc).__name__}: {exc}")
            except Exception:
                # Запись отказа сама может не пройти (закрытое соединение,
                # удалённая строка задачи). Тогда исходное исключение уже
                # залогировано, и терять run_once из-за этого нельзя:
                # наружу оно уходит в брошенный future и пропадает молча.
                log.exception("не удалось пометить задачу %s упавшей", job.id)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        return True

    def stop(self) -> None:
        self._stopped = True

    async def run_forever(self, poll_interval: float = 0.5) -> None:
        """Цикл опроса. Обработка идёт в пуле потоков, чтобы не блокировать
        событийный цикл FastAPI на десятки секунд."""
        while not self._stopped:
            try:
                did_work = await asyncio.to_thread(self.run_once)
            except Exception:
                # Цикл обязан пережить всё: иначе один сбой навсегда
                # останавливает обработку, а сервис продолжает отвечать по HTTP.
                log.exception("непредвиденный сбой в цикле обработки")
                did_work = False
            if not did_work:
                await asyncio.sleep(poll_interval)
