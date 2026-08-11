import pytest

from karaoke_api.audio.probe import AudioInfo, UnsupportedAudio, probe_audio


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
