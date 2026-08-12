from pathlib import Path

from karaoke_api.config import Settings


def test_defaults_match_spec():
    s = Settings()
    assert s.max_duration_sec == 600
    assert s.max_upload_bytes == 209715200
    assert s.file_ttl_hours == 24
    assert s.allowed_formats == ("mp3", "wav", "flac")
    assert s.separator == "demucs"


def test_size_limit_does_not_bind_before_duration_limit():
    """Лимит размера не должен отказывать раньше обещанной длительности.

    Лимит размера — защита от исчерпания ресурсов, длительность — продуктовое
    обещание. Пока защита срабатывает первой, сервис отказывает на шести
    минутах, обещая десять, и причина отказа («слишком большой») не совпадает
    с настоящей. Здесь это зафиксировано числом, чтобы лимиты не разъехались
    молча при следующей правке любого из них.

    Hi-res 96 кГц/24 бит сознательно не в списке: он упирается в размер на
    364 с, и это задокументированная граница.
    """
    s = Settings()
    reference_formats = {
        "wav 44,1 кГц/16 бит стерео": 44100 * 2 * 2,
        "wav 48 кГц/24 бит стерео": 48000 * 3 * 2,
    }
    for name, bytes_per_sec in reference_formats.items():
        needed = s.max_duration_sec * bytes_per_sec
        assert needed <= s.max_upload_bytes, (
            f"{name}: трек в max_duration_sec ({s.max_duration_sec} с) весит "
            f"{needed} Б и не влезает в max_upload_bytes "
            f"({s.max_upload_bytes} Б) — сервис откажет по размеру раньше, "
            f"чем по длительности"
        )


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
