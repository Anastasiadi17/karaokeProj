import numpy as np
import pytest
import soundfile as sf

from karaoke_api.audio import probe
from karaoke_api.audio.probe import (
    AudioInfo,
    UnsupportedAudio,
    normalize_format,
    probe_audio,
)


def test_reads_wav_metadata(make_wav):
    path = make_wav(duration_sec=2.0, sample_rate=44100, channels=2)
    info = probe_audio(path)
    assert isinstance(info, AudioInfo)
    assert info.sample_rate == 44100
    assert info.channels == 2
    assert info.format == "wav"
    assert abs(info.duration_sec - 2.0) < 0.05


def test_mono_is_reported_as_one_channel(make_wav):
    info = probe_audio(make_wav(channels=1))
    assert info.channels == 1


def test_non_audio_raises(tmp_path):
    junk = tmp_path / "notaudio.mp3"
    junk.write_bytes(b"this is definitely not audio")
    with pytest.raises(UnsupportedAudio):
        probe_audio(junk)


def test_extension_is_ignored_content_decides(make_wav, tmp_path):
    wav = make_wav(name="real.wav")
    renamed = tmp_path / "liar.mp3"
    renamed.write_bytes(wav.read_bytes())
    info = probe_audio(renamed)
    assert info.format == "wav"


@pytest.mark.parametrize("container", ["wav", "wavex", "rf64", "w64"])
def test_wav_family_containers_normalize_to_wav(container):
    assert normalize_format(container) == "wav"


@pytest.mark.parametrize("container", ["mp3", "flac", "ogg", "aiff"])
def test_other_containers_pass_through(container):
    assert normalize_format(container) == container


def test_wavex_is_probed_as_its_own_container(tmp_path):
    """probe сохраняет настоящее имя контейнера — сведение делает политика."""
    path = tmp_path / "extensible.wav"
    sf.write(path, np.zeros((44100, 2), dtype="float32"), 44100, format="WAVEX")
    assert probe_audio(path).format == "wavex"


def test_zero_samplerate_is_unsupported_not_a_crash(tmp_path, monkeypatch):
    """samplerate == 0 в заголовке — путь недоверенного ввода. Деление вне
    try дало бы ZeroDivisionError и 500 вместо 400 unsupported_format."""
    class _ZeroInfo:
        frames = 1000
        samplerate = 0
        channels = 2
        format = "WAV"

    monkeypatch.setattr(probe, "sf", type("_SF", (), {
        "info": staticmethod(lambda path: _ZeroInfo()),
    }))

    with pytest.raises(UnsupportedAudio):
        probe_audio(tmp_path / "whatever.wav")
