from dataclasses import dataclass
from pathlib import Path

import soundfile as sf


class UnsupportedAudio(Exception):
    """Файл не является читаемым аудио."""


@dataclass(frozen=True)
class AudioInfo:
    duration_sec: float
    sample_rate: int
    channels: int
    format: str


def probe_audio(path: Path) -> AudioInfo:
    """Разобрать аудиофайл по содержимому. Расширение игнорируется."""
    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise UnsupportedAudio(str(exc)) from exc

    return AudioInfo(
        duration_sec=float(info.frames) / float(info.samplerate),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        format=str(info.format).lower(),
    )
