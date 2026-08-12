from pathlib import Path

from karaoke_api.config import Settings


def test_defaults_match_spec():
    s = Settings()
    assert s.max_duration_sec == 600
    assert s.max_upload_bytes == 104857600
    assert s.file_ttl_hours == 24
    assert s.allowed_formats == ("mp3", "wav", "flac")
    assert s.separator == "demucs"


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("KARAOKE_SEPARATOR", "fake")
    monkeypatch.setenv("KARAOKE_MAX_DURATION_SEC", "30")
    s = Settings()
    assert s.separator == "fake"
    assert s.max_duration_sec == 30


def test_data_paths_are_pathlib(tmp_path, monkeypatch):
    monkeypatch.setenv("KARAOKE_DATA_DIR", str(tmp_path))
    s = Settings()
    assert isinstance(s.data_dir, Path)
    assert s.data_dir == tmp_path
