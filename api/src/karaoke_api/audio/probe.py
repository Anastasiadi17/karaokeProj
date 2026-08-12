from dataclasses import dataclass
from pathlib import Path

import soundfile as sf


class UnsupportedAudio(Exception):
    """Файл не является читаемым аудио."""


# libsndfile возвращает имя КОНТЕЙНЕРА, а allowed_formats — список
# пользовательских форматов (по сути расширений). Для семейства wav это
# разные словари: WAVEX — это WAVE_FORMAT_EXTENSIBLE, обычный .wav, который
# штатно выдают многие редакторы и рекордеры; RF64 и W64 — те же данные RIFF
# с 64-битными размерами для файлов свыше 4 ГБ. Все три libsndfile читает без
# проблем, и обрабатываем мы их одинаково. Без сведения к одному имени сервис
# отвечал «формат не поддерживается» на файл, который прекрасно умеет
# обработать (замер на libsndfile 1.2.2: 'WAVEX' -> wavex -> 400).
_WAV_FAMILY = frozenset({"wav", "wavex", "rf64", "w64"})


def normalize_format(container: str) -> str:
    """Свести имя контейнера от libsndfile к имени формата из политики."""
    return "wav" if container in _WAV_FAMILY else container


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
        # Деление обязано быть внутри try: путь недоверенного ввода, и
        # samplerate == 0 в заголовке дал бы ZeroDivisionError, то есть 500
        # вместо честного 400 unsupported_format.
        duration_sec = float(info.frames) / float(info.samplerate)
    except Exception as exc:
        raise UnsupportedAudio(str(exc)) from exc

    return AudioInfo(
        duration_sec=duration_sec,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        format=str(info.format).lower(),
    )
