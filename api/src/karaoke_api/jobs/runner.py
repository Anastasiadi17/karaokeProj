import asyncio
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..separation.base import StemSeparator
from ..storage.base import Storage
from ..track_lock import TrackLock
from .models import Stage
from .store import JobStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WarmupStatus:
    """Состояние разового прогрева разделителя.

    state — ровно четыре значения: pending (цикл ещё не стартовал), loading
    (идёт прогрев), ready (прогрет), failed (не удался). detail заполняется
    только при failed и несёт тип и текст исключения. elapsed_sec
    заполняется при ready и failed (сколько прошло до отказа), при pending и
    loading равен None.

    Класс неизменяемый намеренно: наружу публикуется объект целиком, и
    читатель из событийного цикла не может застать половинчатое состояние.
    """

    state: str
    detail: str | None = None
    elapsed_sec: float | None = None


class JobRunner:
    """Забирает задачи из очереди и прогоняет их через разделитель.

    Работает в одном экземпляре: GPU один, параллелить нечего.
    """

    def __init__(self, store: JobStore, storage: Storage,
                 separator: StemSeparator, work_dir: Path,
                 track_lock: TrackLock | None = None) -> None:
        self._store = store
        self._storage = storage
        self._separator = separator
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        # Замок необязателен только ради тестов, конструирующих раннер
        # напрямую. Главный код обязан передавать общий с удалением: иначе у
        # каждого свой, гонка возвращается, и заметить это нечем. Проводку
        # закрепляет test_runner_shares_the_track_lock_with_app_state.
        self._track_lock = track_lock or TrackLock()
        self._stopped = False
        self._warmup_status = WarmupStatus("pending")
        # Взведено, пока рабочий поток ничего не считает. Нужно при
        # выключении: см. wait_until_idle.
        self._idle = threading.Event()
        self._idle.set()

    @property
    def warmup_status(self) -> WarmupStatus:
        """Читается из событийного цикла, пишется рабочим потоком.

        Замок не нужен: публикуется неизменяемый объект целиком, а
        присваивание ссылки в CPython атомарно.
        """
        return self._warmup_status

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

            # Критическая секция: проверка строки, запись стемов и finish
            # обязаны быть неделимы относительно удаления. Иначе DELETE
            # успевает целиком между проверкой и записью, и каталог трека
            # переживает строку — такие файлы не видит ни выдача стемов
            # (спрашивает базу), ни уборка по TTL (ходит по строкам).
            # Подробности — в спеке 2026-08-13-delete-race-design.md.
            #
            # finish внутри секции намеренно: тогда инвариант «стемы на
            # диске ⟺ задача done» держится целиком, а не почти.
            with self._track_lock.hold():
                if self._store.get_track(job.track_id) is None:
                    # Трек удалили, пока задача считалась (DELETE или
                    # автоочистка по TTL). Строки задачи тоже уже нет,
                    # помечать нечего.
                    log.info("трек %s удалён во время обработки, стемы не "
                             "пишем", job.track_id)
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

    def _warmup(self) -> None:
        """Разовый прогрев разделителя перед циклом опроса.

        Исключение наружу не выпускается намеренно: сервис без модели всё
        равно принимает загрузки, отдаёт уже готовые стемы и убирает мусор, а
        задача упадёт на своей загрузке модели и получит честную причину в
        поле error. Та же линия, что у check_gpu, который по контракту не
        валит старт ни при каких обстоятельствах.
        """
        self._warmup_status = WarmupStatus("loading")
        started = time.perf_counter()
        try:
            self._separator.warmup()
        except Exception as exc:
            log.exception("прогрев разделителя не удался")
            self._warmup_status = WarmupStatus(
                "failed",
                f"{type(exc).__name__}: {exc}",
                time.perf_counter() - started,
            )
            return
        elapsed = time.perf_counter() - started
        self._warmup_status = WarmupStatus("ready", None, elapsed)
        log.info("разделитель прогрет за %.1f с", elapsed)

    async def run_forever(self, poll_interval: float = 0.5) -> None:
        """Цикл опроса. Обработка идёт в пуле потоков, чтобы не блокировать
        событийный цикл FastAPI на десятки секунд.

        Прогрев — первым делом и в том же пуле. Тогда модели касается ровно
        один поток за всю жизнь процесса, и гонки за её загрузку нет по
        построению, а не по факту установленного замка. Приём HTTP при этом
        не задерживается: событийный цикл свободен, а задача, поставленная
        во время прогрева, дождётся его в очереди — модель ей всё равно
        нужна.

        Выключение во время прогрева: task.cancel() снимает только ожидание
        to_thread, рабочий поток дозагружает модель. Базы прогрев не
        касается, поэтому терять нечего (в отличие от run_once, ради
        которого существует wait_until_idle), а ждать процесс будет не
        дольше самой загрузки.
        """
        if not self._stopped:
            await asyncio.to_thread(self._warmup)
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
