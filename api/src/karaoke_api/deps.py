from dataclasses import dataclass
from pathlib import Path

from .accounts.store import AccountStore
from .config import Settings
from .gpu import GpuStatus
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .separation.base import StemSeparator
from .separation.fake import FakeSeparator
from .storage.local import LocalStorage
from .track_lock import TrackLock


def build_separator(settings: Settings,
                    gpu: GpuStatus | None = None) -> StemSeparator:
    """Собрать разделитель, считаясь с результатом проверки GPU.

    Без gpu DemucsSeparator выбирает устройство сам по
    torch.cuda.is_available(), а тот возвращает True и на сборке без ядер
    под нашу архитектуру — зонд для того и считает настоящую арифметику.
    Игнорировать его вердикт значило бы писать в лог «обработка пойдёт на
    CPU» и всё равно уходить на cuda, роняя каждую задачу на no kernel
    image. Спека (§6) требует ровно обратного: при неудаче — продолжать
    на CPU.
    """
    if settings.separator == "fake":
        return FakeSeparator()
    from .separation.demucs_local import DemucsSeparator

    if gpu is not None and not gpu.available:
        return DemucsSeparator(device="cpu")
    return DemucsSeparator()


@dataclass
class AppState:
    settings: Settings
    store: JobStore
    accounts: AccountStore
    storage: LocalStorage
    separator: StemSeparator
    runner: JobRunner
    track_lock: TrackLock

    @classmethod
    def build(cls, settings: Settings,
              gpu: GpuStatus | None = None) -> "AppState":
        store = JobStore(settings.db_path)
        accounts = AccountStore(settings.db_path)
        storage = LocalStorage(Path(settings.data_dir) / "files")
        separator = build_separator(settings, gpu)
        track_lock = TrackLock()
        runner = JobRunner(
            store, storage, separator, Path(settings.data_dir) / "work",
            track_lock=track_lock,
        )
        return cls(settings, store, accounts, storage, separator, runner,
                   track_lock)
