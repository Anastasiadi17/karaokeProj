"""Воронка: сколько людей дошло до каждого шага.

Считаются люди, а не нажатия: конверсия — это доля людей. Первые дни воронки
не восстановить задним числом, поэтому события пишутся до первого живого
пользователя, а не когда до них дойдут руки.
"""

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from karaoke_api.config import Settings
from karaoke_api.main import create_app

# Заголовки ходят в ASCII: кириллица в токене падает уже в HTTP-клиенте.
TOKEN = "secret-metrics-token"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        metrics_token=TOKEN,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _funnel(client):
    return client.get("/api/funnel",
                      headers={"x-metrics-token": TOKEN}).json()


def test_link_request_and_sign_in_are_counted(client, caplog):
    import re

    with caplog.at_level("INFO"):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})
        match = re.search(r"(/api/auth/callback\?token=\S+)", caplog.text)
    client.get(match.group(1))

    report = _funnel(client)

    assert report["auth_link_requested"] == 1
    assert report["signed_in"] == 1


def test_upload_is_counted(client, make_wav):
    login(client, "ivan@example.com")
    with open(make_wav(duration_sec=1.0), "rb") as fh:
        client.post("/api/tracks", files={"file": ("s.wav", fh, "audio/wav")})

    assert _funnel(client)["track_uploaded"] == 1


def test_people_are_counted_not_clicks(client, make_wav):
    """Два трека одного человека — это один дошедший, а не два."""
    login(client, "ivan@example.com")
    wav = make_wav(duration_sec=1.0)
    for _ in range(2):
        with open(wav, "rb") as fh:
            client.post("/api/tracks", files={"file": ("s.wav", fh, "audio/wav")})

    assert _funnel(client)["track_uploaded"] == 1


def test_client_side_export_reaches_the_funnel(client):
    # Сведение идёт в браузере, и сервер иначе не узнает, дошёл ли человек до
    # результата, — а это последний шаг воронки.
    login(client, "ivan@example.com")

    assert client.post("/api/events/mix_exported").status_code == 204
    assert _funnel(client)["mix_exported"] == 1


def test_unknown_event_is_refused(client):
    login(client, "ivan@example.com")

    response = client.post("/api/events/что-угодно")

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_event"


def test_client_event_needs_a_session(client):
    assert client.post("/api/events/mix_exported").status_code == 401


def test_report_is_closed_without_the_token(client):
    assert client.get("/api/funnel").status_code == 401


def test_report_is_off_when_no_token_configured(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/funnel",
                              headers={"x-metrics-token": "whatever"})

    assert response.status_code == 503


def test_deleted_account_does_not_rewrite_yesterday(client, make_wav):
    """История не должна меняться задним числом при каждом удалении."""
    login(client, "ivan@example.com")
    with open(make_wav(duration_sec=1.0), "rb") as fh:
        client.post("/api/tracks", files={"file": ("s.wav", fh, "audio/wav")})

    client.delete("/api/me")

    assert _funnel(client)["track_uploaded"] == 1
