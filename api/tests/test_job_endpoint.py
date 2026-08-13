import time

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
        free_monthly_operations=100,
    )
    with TestClient(create_app(settings)) as c:
        login(c)
        yield c


def _upload(client, path):
    with open(path, "rb") as fh:
        return client.post("/api/tracks",
                           files={"file": ("s.wav", fh, "audio/wav")}).json()


def _wait_done(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"задача не завершилась за {timeout} с")


def test_unknown_job_is_404(client):
    response = client.get("/api/jobs/deadbeef")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_job_reaches_done_with_stems(client, make_wav):
    ids = _upload(client, make_wav(duration_sec=1.0))
    body = _wait_done(client, ids["job_id"])

    assert body["status"] == "done"
    assert body["stage"] is None
    assert body["progress"] == 1.0
    assert body["error"] is None
    assert set(body["result"]["stems"]) == {"vocals", "no_vocals"}
    assert body["result"]["degraded"] is False


def test_response_shape_is_stable(client, make_wav):
    ids = _upload(client, make_wav(duration_sec=1.0))
    body = client.get(f"/api/jobs/{ids['job_id']}").json()
    assert set(body) == {"status", "stage", "progress", "error", "result"}


def test_running_job_reports_stage_as_plain_string(client):
    from karaoke_api.jobs.models import Stage
    from karaoke_api.jobs.store import new_id

    state = client.app.state.karaoke

    # Фоновый цикл остановлен, иначе он перехватит задачу и доведёт её до done.
    # stop() проверяется в начале итерации, поэтому ждём чуть дольше интервала
    # опроса — так тест детерминирован, а не «обычно успевает».
    state.runner.stop()
    time.sleep(0.6)

    track_id = new_id()
    state.store.create_track(
        track_id, "s.wav", f"tracks/{track_id}/original.wav", 1.0
    )
    job_id = state.store.create_job(track_id)
    state.store.claim_next()
    state.store.set_stage(job_id, Stage.SEPARATING, 0.42)

    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "running"
    assert body["stage"] == "separating"
    assert isinstance(body["stage"], str) and not isinstance(body["stage"], Stage)
    assert body["progress"] == pytest.approx(0.42)
