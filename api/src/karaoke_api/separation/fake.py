import shutil
from pathlib import Path

from .base import ProgressCallback, SeparationResult


class FakeSeparator:
    """Подделка для тестов: копирует исходник в обе дорожки.

    Нужна, чтобы весь конвейер — очередь, стадии, выдача файлов — можно было
    проверить за миллисекунды без GPU и без настоящей модели.
    """

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult:
        on_progress("loading", 0.0)
        on_progress("separating", 0.5)

        vocals = Path(out_dir) / "vocals.wav"
        no_vocals = Path(out_dir) / "no_vocals.wav"

        on_progress("writing", 0.9)
        shutil.copyfile(source, vocals)
        shutil.copyfile(source, no_vocals)

        return SeparationResult(vocals=vocals, no_vocals=no_vocals)
