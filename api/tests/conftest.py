import math
import threading
import time
import wave
from pathlib import Path

import pytest

from karaoke_api.separation.fake import FakeSeparator


def _write_wav(path: Path, duration_sec: float, sample_rate: int = 44100,
               channels: int = 2, freq: float = 440.0) -> Path:
    frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = bytearray()
        for i in range(frames):
            value = int(20000 * math.sin(2 * math.pi * freq * i / sample_rate))
            data += value.to_bytes(2, "little", signed=True) * channels
        wf.writeframes(bytes(data))
    return path


class SlowSeparator:
    """Считает заметно дольше, чем длится обращение к HTTP-эндпоинту.

    FakeSeparator укладывается в микросекунды, поэтому окна «выключение
    попало в работающую задачу» и «трек удалили во время обработки» на нём
    не воспроизвести. С настоящим demucs эти окна длятся десятки секунд,
    то есть штатный Ctrl+C и обычный DELETE попадают в них почти всегда.
    """

    def __init__(self, delay: float = 1.0) -> None:
        self._delay = delay
        self.started = threading.Event()

    def warmup(self) -> None:
        """Греть нечего: подделка просто спит в separate."""

    def separate(self, source, out_dir, on_progress):
        self.started.set()
        time.sleep(self._delay)
        return FakeSeparator().separate(source, out_dir, on_progress)


@pytest.fixture
def slow_separator():
    return SlowSeparator(delay=1.0)


@pytest.fixture
def make_wav(tmp_path):
    """Создаёт настоящий WAV заданной длительности. Возвращает Path."""

    def _make(name: str = "t.wav", duration_sec: float = 1.0,
              sample_rate: int = 44100, channels: int = 2) -> Path:
        return _write_wav(tmp_path / name, duration_sec, sample_rate, channels)

    return _make
