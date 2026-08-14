import pytest
from fastapi.testclient import TestClient

from karaoke_api.accounts.ratelimit import RateLimiter
from karaoke_api.config import Settings
from karaoke_api.main import create_app


def test_allows_up_to_the_limit():
    limiter = RateLimiter(limit=3, window_sec=60)

    assert [limiter.allow("k", now=0) for _ in range(3)] == [True] * 3


def test_refuses_beyond_the_limit():
    limiter = RateLimiter(limit=2, window_sec=60)
    limiter.allow("k", now=0)
    limiter.allow("k", now=1)

    assert limiter.allow("k", now=2) is False


def test_window_slides():
    """Час прошёл — счёт начинается заново, иначе адрес блокируется навсегда."""
    limiter = RateLimiter(limit=1, window_sec=60)
    limiter.allow("k", now=0)

    assert limiter.allow("k", now=30) is False
    assert limiter.allow("k", now=61) is True


def test_keys_do_not_share_the_counter():
    limiter = RateLimiter(limit=1, window_sec=60)
    limiter.allow("ivan@example.com", now=0)

    assert limiter.allow("petr@example.com", now=0) is True


def test_refused_attempt_does_not_extend_the_ban():
    """Иначе долбящий клиент запирает себя навсегда, а живой человек рядом —
    заодно с ним."""
    limiter = RateLimiter(limit=1, window_sec=60)
    limiter.allow("k", now=0)
    for moment in range(1, 60):
        limiter.allow("k", now=moment)

    assert limiter.allow("k", now=61) is True


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        login_requests_per_email_hour=2,
        login_requests_per_ip_hour=100,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_endpoint_refuses_the_flood(client):
    for _ in range(2):
        assert client.post("/api/auth/request",
                           json={"email": "ivan@example.com"}).status_code == 204

    response = client.post("/api/auth/request",
                           json={"email": "ivan@example.com"})

    assert response.status_code == 429
    assert response.json()["error"] == "too_many_requests"


def test_other_address_is_not_punished_for_the_neighbour(client):
    for _ in range(3):
        client.post("/api/auth/request", json={"email": "ivan@example.com"})

    response = client.post("/api/auth/request",
                           json={"email": "petr@example.com"})

    assert response.status_code == 204


def test_ip_limit_covers_many_addresses(tmp_path):
    """Счёт по адресу не спасает от рассылки по сотне разных адресов."""
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        login_requests_per_email_hour=100,
        login_requests_per_ip_hour=2,
    )
    with TestClient(create_app(settings)) as client:
        for i in range(2):
            assert client.post(
                "/api/auth/request", json={"email": f"a{i}@example.com"}
            ).status_code == 204

        response = client.post("/api/auth/request",
                               json={"email": "a99@example.com"})

    assert response.status_code == 429


# --- удаление аккаунта -------------------------------------------------


def test_account_deletion_needs_a_session(client):
    assert client.delete("/api/me").status_code == 401


def test_deleted_account_takes_everything_with_it(client, make_wav):
    from .conftest import login

    user = login(client, "ivan@example.com")
    accounts = client.app.state.karaoke.accounts
    accounts.add_credits(user.id, 50, "test")
    with open(make_wav(duration_sec=1.0), "rb") as fh:
        client.post("/api/tracks", files={"file": ("s.wav", fh, "audio/wav")})

    assert client.delete("/api/me").status_code == 204

    # Сессии нет, пользователя нет, кредитов нет.
    assert client.get("/api/me").status_code == 401
    assert accounts.get_user(user.id) is None
    assert accounts.credit_balance(user.id) == 0
    assert client.app.state.karaoke.store.list_tracks_of(user.id) == []
