from dataclasses import dataclass
from pathlib import Path

from .accounts.store import AccountStore
from .config import Settings
from .gpu import GpuStatus
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .separation.base import StemSeparator
from .separation.fake import FakeSeparator
from .storage.base import Storage
from .storage.local import LocalStorage
from .storage.r2 import R2Storage
from .track_lock import TrackLock


def build_separator(settings: Settings,
                    gpu: GpuStatus | None = None,
                    storage: Storage | None = None) -> StemSeparator:
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

    if settings.separator == "runpod":
        missing = [
            name for name in ("runpod_endpoint", "runpod_api_key")
            if not getattr(settings, name)
        ]
        if missing:
            raise ValueError(
                "KARAOKE_SEPARATOR=runpod, но не заданы: "
                + ", ".join(f"KARAOKE_{name.upper()}" for name in missing)
            )
        if settings.storage != "r2":
            # Воркер живёт на чужой машине: локальный диск ему недоступен, и
            # молча уйти на него значит получить задачи, падающие на скачивании
            # исходника, — а виноватым будет выглядеть RunPod.
            raise ValueError(
                "KARAOKE_SEPARATOR=runpod требует KARAOKE_STORAGE=r2: "
                "воркеру нужны файлы, доступные с чужой машины"
            )
        from .separation.runpod_remote import RunpodSeparator

        return RunpodSeparator(
            settings.runpod_endpoint, settings.runpod_api_key, storage,
            timeout_sec=settings.runpod_timeout_sec,
        )
    from .separation.demucs_local import DemucsSeparator

    if gpu is not None and not gpu.available:
        return DemucsSeparator(device="cpu")
    return DemucsSeparator()


def build_storage(settings: Settings) -> Storage:
    """Выбирает хранилище по настройке.

    Неполная настройка R2 — это отказ на старте, а не тихий откат на диск.
    Молчаливый откат хуже падения: файлы лягут на диск, который никто не
    бэкапит, обработка на чужой машине перестанет их находить, и виноватым
    окажется RunPod, а не забытая переменная окружения.
    """
    if settings.storage == "local":
        return LocalStorage(Path(settings.data_dir) / "files")

    if settings.storage == "r2":
        missing = [
            name for name in ("r2_endpoint", "r2_bucket", "r2_access_key",
                              "r2_secret_key")
            if not getattr(settings, name)
        ]
        if missing:
            raise ValueError(
                "KARAOKE_STORAGE=r2, но не заданы: "
                + ", ".join(f"KARAOKE_{name.upper()}" for name in missing)
            )
        return R2Storage(
            settings.r2_endpoint, settings.r2_bucket,
            settings.r2_access_key, settings.r2_secret_key,
        )

    raise ValueError(
        f"неизвестное хранилище {settings.storage!r}: ожидается local или r2"
    )


@dataclass
class AppState:
    settings: Settings
    store: JobStore
    accounts: AccountStore
    storage: Storage
    separator: StemSeparator
    runner: JobRunner
    track_lock: TrackLock

    @classmethod
    def build(cls, settings: Settings,
              gpu: GpuStatus | None = None) -> "AppState":
        store = JobStore(settings.db_path)
        accounts = AccountStore(settings.db_path)
        storage = build_storage(settings)
        separator = build_separator(settings, gpu, storage)
        track_lock = TrackLock()
        runner = JobRunner(
            store, storage, separator, Path(settings.data_dir) / "work",
            track_lock=track_lock,
        )
        return cls(settings, store, accounts, storage, separator, runner,
                   track_lock)
