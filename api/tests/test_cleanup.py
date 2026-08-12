from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from karaoke_api.cleanup import purge_expired
from karaoke_api.config import Settings
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.main import create_app
from karaoke_api.storage.local import LocalStorage


@pytest.fixture
def wiring(tmp_path, make_wav):
    store = JobStore(tmp_path / "db.sqlite")
    storage = LocalStorage(tmp_path / "store")
    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    storage.store_file(key, make_wav(duration_sec=0.5))
    store.create_track(track_id, "s.wav", key, 0.5)
    return store, storage, track_id


def test_fresh_track_survives(wiring):
    store, storage, track_id = wiring
    assert purge_expired(store, storage, ttl_hours=24) == 0
    assert store.get_track(track_id) is not None


def test_expired_track_is_removed(wiring):
    store, storage, track_id = wiring
    future = datetime.now(timezone.utc) + timedelta(hours=25)

    assert purge_expired(store, storage, ttl_hours=24, now=future) == 1

    assert store.get_track(track_id) is None
    assert not storage.exists(f"tracks/{track_id}/original.wav")


def test_delete_endpoint_removes_track(tmp_path, make_wav):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()

        assert client.delete(f"/api/tracks/{ids['track_id']}").status_code == 204
        assert client.get(f"/api/jobs/{ids['job_id']}").status_code == 404


def test_delete_unknown_track_is_404(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        assert client.delete("/api/tracks/nope").status_code == 404


def test_purge_continues_after_one_track_fails(wiring, monkeypatch, make_wav):
    """Один неудалимый трек (например, файл занят фоновым процессом на
    Windows) не должен обрывать всю уборку: остальные истёкшие треки
    обязаны быть удалены, а счётчик — отражать только реально удалённое."""
    store, storage, track_id = wiring

    other_id = new_id()
    other_key = f"tracks/{other_id}/original.wav"
    storage.store_file(other_key, make_wav(name="other.wav", duration_sec=0.5))
    store.create_track(other_id, "o.wav", other_key, 0.5)

    real_delete_prefix = storage.delete_prefix
    calls = []

    def flaky_delete_prefix(prefix):
        calls.append(prefix)
        if prefix == f"tracks/{track_id}":
            raise PermissionError("simulated: file in use")
        return real_delete_prefix(prefix)

    monkeypatch.setattr(storage, "delete_prefix", flaky_delete_prefix)

    future = datetime.now(timezone.utc) + timedelta(hours=25)
    removed = purge_expired(store, storage, ttl_hours=24, now=future)

    assert removed == 1
    # первый трек не удалился ни из хранилища, ни из БД (ошибка была до
    # delete_track), второй — удалён полностью.
    assert store.get_track(track_id) is not None
    assert store.get_track(other_id) is None
    assert not storage.exists(other_key)
