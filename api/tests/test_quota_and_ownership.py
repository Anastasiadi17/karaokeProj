import pytest
from fastapi.testclient import TestClient

from .conftest import login
from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        free_monthly_operations=3,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _upload(client, path, name="song.wav"):
    with open(path, "rb") as fh:
        return client.post("/api/tracks", files={"file": (name, fh, "audio/wav")})


# --- лимит -------------------------------------------------------------


def test_upload_without_session_is_401(client, make_wav):
    response = _upload(client, make_wav(duration_sec=1.0))

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_fourth_upload_of_the_month_is_refused(client, make_wav):
    login(client)
    wav = make_wav(duration_sec=1.0)

    for _ in range(3):
        assert _upload(client, wav).status_code == 201

    response = _upload(client, wav)

    assert response.status_code == 429
    assert response.json()["error"] == "quota_exceeded"


def test_refused_upload_does_not_eat_the_quota(client, make_wav, tmp_path):
    """Отказ по формату — не операция.

    Иначе человек, трижды промахнувшийся файлом, остаётся без месяца.
    """
    login(client)
    junk = tmp_path / "junk.wav"
    junk.write_bytes("это точно не аудио".encode("utf-8"))

    for _ in range(3):
        assert _upload(client, junk).status_code == 400

    assert _upload(client, make_wav(duration_sec=1.0)).status_code == 201


def test_quota_is_counted_per_user(client, make_wav):
    wav = make_wav(duration_sec=1.0)
    login(client, "ivan@example.com")
    for _ in range(3):
        _upload(client, wav)
    assert _upload(client, wav).status_code == 429

    login(client, "petr@example.com")

    assert _upload(client, wav).status_code == 201


def test_me_shows_the_remaining_quota(client, make_wav):
    login(client)
    _upload(client, make_wav(duration_sec=1.0))

    body = client.get("/api/me").json()

    assert body["operations_used"] == 1
    assert body["operations_limit"] == 3


# --- владелец ----------------------------------------------------------


def _wait_done(client, job_id, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body.get("status") in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("задача не завершилась")


def test_foreign_track_does_not_exist_for_the_stranger(client, make_wav):
    """404, а не 403: подтверждать существование чужого трека незачем."""
    login(client, "ivan@example.com")
    body = _upload(client, make_wav(duration_sec=1.0)).json()
    _wait_done(client, body["job_id"])

    login(client, "petr@example.com")

    assert client.get(
        f"/api/tracks/{body['track_id']}/stems/no_vocals"
    ).status_code == 404
    assert client.get(f"/api/jobs/{body['job_id']}").status_code == 404
    assert client.delete(f"/api/tracks/{body['track_id']}").status_code == 404


def test_owner_still_gets_everything(client, make_wav):
    login(client, "ivan@example.com")
    body = _upload(client, make_wav(duration_sec=1.0)).json()
    _wait_done(client, body["job_id"])

    assert client.get(
        f"/api/tracks/{body['track_id']}/stems/no_vocals"
    ).status_code == 200
    assert client.delete(f"/api/tracks/{body['track_id']}").status_code == 204


def test_ownerless_track_stays_available(client, make_wav):
    """Треки, загруженные до аккаунтов, доживают до уборки по сроку.

    Осознанная уступка: миграции данных нет, а ломать чужую вкладку задним
    числом хуже, чем на сутки оставить доступ по идентификатору.
    """
    login(client, "ivan@example.com")
    body = _upload(client, make_wav(duration_sec=1.0)).json()
    _wait_done(client, body["job_id"])

    state = client.app.state.karaoke
    with state.store._lock:  # noqa: SLF001 — тест правит то, чего API не умеет
        state.store._conn.execute(
            "UPDATE tracks SET user_id = NULL WHERE id = ?",
            (body["track_id"],),
        )
        state.store._conn.commit()

    login(client, "petr@example.com")

    assert client.get(
        f"/api/tracks/{body['track_id']}/stems/no_vocals"
    ).status_code == 200
