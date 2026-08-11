from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

ProgressCallback = Callable[[str, float], None]
"""Вызывается как on_progress(stage, fraction). fraction в диапазоне 0..1."""


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    no_vocals: Path


class StemSeparator(Protocol):
    """Отделение вокала от аккомпанемента.

    Реализация обязана положить ровно два файла в out_dir с именами
    vocals.wav и no_vocals.wav и вызвать on_progress со стадиями
    loading, separating, writing в этом порядке.
    """

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult: ...
