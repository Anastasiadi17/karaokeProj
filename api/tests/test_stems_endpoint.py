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


@pytest.fixture
def ready_track(client, make_wav):
    with open(make_wav(duration_sec=1.0), "rb") as fh:
        ids = client.post("/api/tracks",
                          files={"file": ("s.wav", fh, "audio/wav")}).json()
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/jobs/{ids['job_id']}").json()["status"] == "done":
            return ids["track_id"]
        time.sleep(0.05)
    raise AssertionError("задача не завершилась")


def test_full_download(client, ready_track):
    response = client.get(f"/api/tracks/{ready_track}/stems/no_vocals")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == int(response.headers["content-length"])


def test_range_request_returns_206(client, ready_track):
    response = client.get(
        f"/api/tracks/{ready_track}/stems/no_vocals",
        headers={"Range": "bytes=0-99"},
    )
    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.headers["content-range"].startswith("bytes 0-99/")


def test_unsatisfiable_range_returns_416(client, ready_track):
    response = client.get(
        f"/api/tracks/{ready_track}/stems/no_vocals",
        headers={"Range": "bytes=99999999-"},
    )
    assert response.status_code == 416


def test_unknown_kind_is_404(client, ready_track):
    assert client.get(f"/api/tracks/{ready_track}/stems/drums").status_code == 404


def test_unknown_track_is_404(client):
    assert client.get("/api/tracks/nope/stems/vocals").status_code == 404


def test_stem_of_deleted_track_is_404_even_if_file_survives(client, ready_track):
    """Хранилище — не источник истины. Файлы могут пережить удаление строки
    (гонка с работающей задачей, сбой уборки), но для клиента трек удалён."""
    state = client.app.state.karaoke
    key = f"tracks/{ready_track}/stems/no_vocals.wav"
    assert state.storage.exists(key)

    # Только строка в базе — файл намеренно оставляем на диске.
    state.store.delete_track(ready_track)
    assert state.storage.exists(key), "тест бессмыслен, если файла уже нет"

    response = client.get(f"/api/tracks/{ready_track}/stems/no_vocals")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
