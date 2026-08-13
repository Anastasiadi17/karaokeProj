import re

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app

SESSION_COOKIE = "karaoke_session"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _link_from_log(caplog) -> str:
    """Письмо на этом этапе — строка в логе; она же транспорт для теста.

    Возвращается путь, а не полный адрес: ссылка собрана из
    `public_base_url`, и запрос по чужому хосту положил бы куку не на тот
    домен, откуда её потом никто не пришлёт.
    """
    match = re.search(r"https?://\S+?(/api/auth/callback\?token=\S+)",
                      caplog.text)
    assert match, f"ссылки нет в логе: {caplog.text}"
    return match.group(1)


def test_request_answers_the_same_for_known_and_unknown(client, caplog):
    """Иначе эндпоинт превращается в проверялку «есть ли такой пользователь».

    Отвечать по-разному — значит раздать список клиентов любому желающему.
    """
    with caplog.at_level("INFO"):
        first = client.post("/api/auth/request",
                            json={"email": "ivan@example.com"})
        client.get(_link_from_log(caplog))
        second = client.post("/api/auth/request",
                             json={"email": "ivan@example.com"})
        third = client.post("/api/auth/request",
                            json={"email": "nobody@example.com"})

    assert first.status_code == 204
    assert second.status_code == 204
    assert third.status_code == 204
    assert first.content == third.content == b""


def test_bad_email_is_rejected(client):
    response = client.post("/api/auth/request", json={"email": "не адрес"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_email"


def test_link_from_the_letter_logs_in(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})
        link = _link_from_log(caplog)

    response = client.get(link,
                          follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


def test_link_works_once(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})
        path = _link_from_log(caplog)

    client.get(path, follow_redirects=False)
    second = client.get(path, follow_redirects=False)

    assert second.status_code == 400
    assert second.json()["error"] == "invalid_token"


def test_broken_token_is_a_refusal_not_a_crash(client):
    response = client.get("/api/auth/callback?token=мусор",
                          follow_redirects=False)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_token"


def test_logout_ends_the_session(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})
        path = _link_from_log(caplog)
    client.get(path)

    assert client.get("/api/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_me_without_session_is_401(client):
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_me_tells_email_plan_and_quota(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})
        path = _link_from_log(caplog)
    client.get(path)

    body = client.get("/api/me").json()

    assert body["email"] == "ivan@example.com"
    assert body["plan"] == "free"
    assert body["operations_used"] == 0
    assert body["operations_limit"] == 3
