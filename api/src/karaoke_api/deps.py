from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .separation.base import StemSeparator
from .separation.fake import FakeSeparator
from .storage.local import LocalStorage


def build_separator(settings: Settings) -> StemSeparator:
    if settings.separator == "fake":
        return FakeSeparator()
    from .separation.demucs_local import DemucsSeparator

    return DemucsSeparator()


@dataclass
class AppState:
    settings: Settings
    store: JobStore
    storage: LocalStorage
    separator: StemSeparator
    runner: JobRunner

    @classmethod
    def build(cls, settings: Settings) -> "AppState":
        store = JobStore(settings.db_path)
        storage = LocalStorage(Path(settings.data_dir) / "files")
        separator = build_separator(settings)
        runner = JobRunner(
            store, storage, separator, Path(settings.data_dir) / "work"
        )
        return cls(settings, store, storage, separator, runner)
