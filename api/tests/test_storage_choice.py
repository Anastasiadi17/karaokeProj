import pytest

from karaoke_api.config import Settings
from karaoke_api.deps import build_storage
from karaoke_api.storage.local import LocalStorage
from karaoke_api.storage.r2 import R2Storage


def _settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, db_path=tmp_path / "db", **kwargs)


def test_local_by_default(tmp_path):
    assert isinstance(build_storage(_settings(tmp_path)), LocalStorage)


def test_r2_when_fully_configured(tmp_path):
    storage = build_storage(_settings(
        tmp_path, storage="r2", r2_endpoint="https://acc.r2.example",
        r2_bucket="karaoke", r2_access_key="k", r2_secret_key="s",
    ))

    assert isinstance(storage, R2Storage)


def test_incomplete_r2_is_a_refusal_not_a_quiet_fallback(tmp_path):
    """Тихий откат на диск хуже падения: файлы лягут туда, где их не ищет
    обработка на чужой машине, и виноватым окажется RunPod, а не забытая
    переменная окружения."""
    with pytest.raises(ValueError) as exc:
        build_storage(_settings(tmp_path, storage="r2",
                                r2_endpoint="https://acc.r2.example"))

    # В сообщении названо, чего именно не хватает — иначе искать придётся
    # перебором.
    assert "KARAOKE_R2_BUCKET" in str(exc.value)
    assert "KARAOKE_R2_ACCESS_KEY" in str(exc.value)


def test_unknown_storage_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_storage(_settings(tmp_path, storage="дискета"))
