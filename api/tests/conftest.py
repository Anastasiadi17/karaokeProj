import math
import wave
from pathlib import Path

import pytest


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


@pytest.fixture
def make_wav(tmp_path):
    """Создаёт настоящий WAV заданной длительности. Возвращает Path."""

    def _make(name: str = "t.wav", duration_sec: float = 1.0,
              sample_rate: int = 44100, channels: int = 2) -> Path:
        return _write_wav(tmp_path / name, duration_sec, sample_rate, channels)

    return _make
