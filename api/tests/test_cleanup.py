from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from karaoke_api import deps
from karaoke_api.cleanup import purge_expired, purge_orphan_track_dirs
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


def test_orphan_track_dir_is_purged_and_live_track_survives(wiring, make_wav):
    """Каталог трека без строки в базе не увидит ни одна другая уборка:
    и list_expired_tracks, и DELETE ходят по таблице tracks."""
    store, storage, track_id = wiring

    orphan_id = new_id()
    storage.store_file(f"tracks/{orphan_id}/stems/vocals.wav",
                       make_wav(name="orphan.wav", duration_sec=0.5))

    assert purge_orphan_track_dirs(store, storage) == 1

    assert not storage.exists(f"tracks/{orphan_id}/stems/vocals.wav")
    assert storage.exists(f"tracks/{track_id}/original.wav")
    assert storage.list_prefixes("tracks") == [track_id]


def test_orphan_purge_continues_after_one_dir_fails(wiring, monkeypatch,
                                                    make_wav):
    """Занятый файл на Windows не должен обрывать сверку целиком."""
    store, storage, _ = wiring

    stuck_id, ok_id = sorted([new_id(), new_id()])
    for oid in (stuck_id, ok_id):
        storage.store_file(f"tracks/{oid}/stems/vocals.wav",
                           make_wav(name=f"{oid}.wav", duration_sec=0.5))

    real_delete_prefix = storage.delete_prefix

    def flaky_delete_prefix(prefix):
        if prefix == f"tracks/{stuck_id}":
            raise PermissionError("simulated: file in use")
        return real_delete_prefix(prefix)

    monkeypatch.setattr(storage, "delete_prefix", flaky_delete_prefix)

    assert purge_orphan_track_dirs(store, storage) == 1
    assert storage.exists(f"tracks/{stuck_id}/stems/vocals.wav")
    assert not storage.exists(f"tracks/{ok_id}/stems/vocals.wav")


def test_delete_during_processing_leaves_no_files(tmp_path, make_wav,
                                                  monkeypatch, slow_separator):
    """DELETE во время обработки: воркер не должен заново создать каталог
    трека уже после успешного 204."""
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    monkeypatch.setattr(
        deps, "build_separator", lambda s, gpu=None: slow_separator
    )

    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()
        assert slow_separator.started.wait(10), "воркер не успел взять задачу"

        assert client.delete(f"/api/tracks/{ids['track_id']}").status_code == 204

    # Выключение дожидается воркера (см. test_shutdown), поэтому к этому
    # моменту задача точно досчитала и всё, что она хотела записать, записано.
    tracks_root = tmp_path / "data" / "files" / "tracks"
    leftovers = sorted(p for p in tracks_root.rglob("*") if p.is_file())
    assert leftovers == [], f"файлы удалённого трека остались: {leftovers}"


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

    def flaky_delete_prefix(prefix):
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
