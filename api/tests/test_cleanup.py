import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from karaoke_api import deps
from karaoke_api.cleanup import (
    purge_expired,
    purge_orphan_track_dirs,
    purge_track,
)
from karaoke_api.config import Settings
from karaoke_api.deps import AppState
from karaoke_api.jobs.runner import JobRunner
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.main import create_app
from karaoke_api.separation.fake import FakeSeparator
from karaoke_api.storage.local import LocalStorage
from karaoke_api.track_lock import TrackLock


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
    assert purge_expired(store, storage, TrackLock(), ttl_hours=24) == 0
    assert store.get_track(track_id) is not None


def test_expired_track_is_removed(wiring):
    store, storage, track_id = wiring
    future = datetime.now(timezone.utc) + timedelta(hours=25)

    assert purge_expired(store, storage, TrackLock(), ttl_hours=24, now=future) == 1

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


def test_delete_endpoint_reports_locked_files_instead_of_crashing(tmp_path,
                                                                  make_wav,
                                                                  monkeypatch):
    """Заблокированный файл на Windows — штатный сценарий. DELETE обязан
    ответить внятным кодом, а не голым 500, и оставить строку трека, чтобы
    файлы подобрала уборка по TTL, а не превратились в сирот."""
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

        state = client.app.state.karaoke
        monkeypatch.setattr(
            state.storage, "delete_prefix",
            lambda prefix: (_ for _ in ()).throw(
                PermissionError("simulated: WinError 32, file in use")
            ),
        )

        response = client.delete(f"/api/tracks/{ids['track_id']}")

        assert response.status_code == 503
        assert response.json()["error"] == "delete_failed"
        assert state.store.get_track(ids["track_id"]) is not None


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
    removed = purge_expired(store, storage, TrackLock(), ttl_hours=24, now=future)

    assert removed == 1
    # первый трек не удалился ни из хранилища, ни из БД (ошибка была до
    # delete_track), второй — удалён полностью.
    assert store.get_track(track_id) is not None
    assert store.get_track(other_id) is None
    assert not storage.exists(other_key)


def test_runner_shares_the_track_lock_with_app_state(tmp_path):
    """Замок обязан быть общим у раннера и удаления.

    Если раннер заведёт свой, гонка вернётся, а все остальные тесты
    останутся зелёными — ровно тот способ сломаться молча, ради которого
    параметр сделан необязательным. Поэтому проводка закреплена отдельно.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    state = AppState.build(settings)
    try:
        assert state.runner._track_lock is state.track_lock
    finally:
        state.store.close()


def test_delete_answers_503_when_the_lock_is_busy(tmp_path, make_wav):
    """Пока кто-то держит замок, DELETE не висит вечно, а отвечает уже
    существующим кодом. Новых кодов в контракте не появляется."""
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        track_lock_timeout_sec=0.05,
    )
    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()
        state = client.app.state.karaoke
        with state.track_lock.hold():
            response = client.delete(f"/api/tracks/{ids['track_id']}")

    assert response.status_code == 503
    assert response.json() == {"error": "delete_failed"}


class BlockingStorage:
    """Останавливает воркера ровно внутри записи стемов.

    SlowSeparator сюда не годится: он тормозит separate, а остаточное окно
    живёт ПОСЛЕ него — между проверкой строки трека и записью файлов.
    Добраться до окна можно только задержкой внутри store_file.
    """

    def __init__(self, inner):
        self._inner = inner
        self.writing_stems = threading.Event()
        self.may_continue = threading.Event()

    def store_file(self, key, src):
        if "/stems/" in key:
            self.writing_stems.set()
            self.may_continue.wait(10)
        self._inner.store_file(key, src)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_delete_waits_for_stem_write_and_leaves_no_orphan(tmp_path, make_wav):
    """Настоящая гонка: DELETE приходит, когда воркер уже прошёл проверку
    строки и пишет стемы.

    Без замка удаление проходит немедленно, воркер дописывает каталог уже
    после rmtree, и файлы переживают строку. Их не видит ни выдача стемов
    (спрашивает базу), ни уборка по TTL (ходит по строкам) — подбирает
    только сверка каталогов при следующем старте процесса.
    """
    store = JobStore(tmp_path / "db.sqlite")
    inner = LocalStorage(tmp_path / "store")
    storage = BlockingStorage(inner)
    work = tmp_path / "work"
    work.mkdir()
    lock = TrackLock()

    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    inner.store_file(key, make_wav(duration_sec=0.5))
    store.create_track(track_id, "s.wav", key, 0.5)
    store.create_job(track_id)

    runner = JobRunner(store, storage, FakeSeparator(), work, track_lock=lock)

    worker = threading.Thread(target=runner.run_once)
    worker.start()
    assert storage.writing_stems.wait(10), "воркер не дошёл до записи стемов"

    deleter = threading.Thread(
        target=purge_track, args=(store, inner, lock, track_id)
    )
    deleter.start()
    deleter.join(0.5)
    assert deleter.is_alive(), (
        "удаление прошло, пока воркер писал стемы: замок не держится"
    )

    storage.may_continue.set()
    worker.join(10)
    deleter.join(10)

    try:
        assert store.get_track(track_id) is None
        assert inner.list_prefixes("tracks") == [], (
            "каталог трека пережил удаление"
        )
    finally:
        store.close()
