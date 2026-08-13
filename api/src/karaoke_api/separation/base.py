from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

ProgressCallback = Callable[[str, float], None]
"""Вызывается как on_progress(stage, fraction). fraction в диапазоне 0..1."""


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    no_vocals: Path
    degraded: bool = False


class StemSeparator(Protocol):
    """Отделение вокала от аккомпанемента.

    Реализация обязана положить ровно два файла в out_dir с именами
    vocals.wav и no_vocals.wav и вызвать on_progress со стадиями
    loading, separating, writing в этом порядке.

    degraded в возвращённом SeparationResult обязан быть True, только если
    реализация была вынуждена откатиться на более медленный или более слабый
    путь ради того, чтобы вообще выполнить задачу (например, нехватка
    видеопамяти на GPU и повтор на CPU). По умолчанию False; реализации,
    которые деградировать не умеют (например, FakeSeparator), значение не
    трогают.

    warmup обязан довести реализацию до состояния, в котором первая
    настоящая задача не платит разовых издержек. Метод идемпотентен и
    безопасен при повторном вызове; реализации, которым греть нечего, не
    делают ничего. Метод обязательный намеренно: новая реализация
    разделителя не должна иметь возможности молча его пропустить.
    """

    def warmup(self) -> None: ...

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult: ...
