import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from karaoke_api.accounts.billing import BadSignature, verify_signature
from karaoke_api.config import Settings
from karaoke_api.main import create_app

SECRET = "whsec_тест"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        stripe_webhook_secret=SECRET,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _sign(payload: bytes, secret: str = SECRET, at: int | None = None) -> str:
    timestamp = at if at is not None else int(time.time())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _send(client, event: dict, header: str | None = None):
    payload = json.dumps(event).encode("utf-8")
    return client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"stripe-signature": header or _sign(payload)},
    )


def _paid(email: str, event_id: str = "evt_1", subscription: str = "sub_1"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "customer_details": {"email": email},
            "subscription": subscription,
            "current_period_end": int(time.time()) + 30 * 24 * 3600,
        }},
    }


# --- подпись -----------------------------------------------------------


def test_good_signature_passes():
    payload = b'{"id":"evt"}'
    verify_signature(payload, _sign(payload), SECRET)


def test_wrong_secret_is_rejected():
    payload = b'{"id":"evt"}'
    with pytest.raises(BadSignature):
        verify_signature(payload, _sign(payload, secret="другой"), SECRET)


def test_old_signature_is_rejected():
    """Иначе подслушанный запрос переигрывается через месяц и снова включает
    подписку."""
    payload = b'{"id":"evt"}'
    header = _sign(payload, at=int(time.time()) - 3600)

    with pytest.raises(BadSignature):
        verify_signature(payload, header, SECRET)


def test_garbage_header_is_rejected():
    with pytest.raises(BadSignature):
        verify_signature(b"{}", "мусор", SECRET)


# --- вебхук ------------------------------------------------------------


def test_payment_turns_the_plan_pro(client):
    login(client, "ivan@example.com")
    assert client.get("/api/me").json()["plan"] == "free"

    assert _send(client, _paid("ivan@example.com")).status_code == 200

    assert client.get("/api/me").json()["plan"] == "pro"


def test_repeated_delivery_changes_nothing(client):
    """Stripe повторяет доставку после любого нашего таймаута."""
    login(client, "ivan@example.com")
    event = _paid("ivan@example.com")

    _send(client, event)
    second = _send(client, event)

    assert second.status_code == 200
    with client.app.state.karaoke.accounts.connection() as conn:
        rows = conn.execute("SELECT * FROM subscriptions").fetchall()
    assert len(rows) == 1


def test_bad_signature_is_400(client):
    login(client, "ivan@example.com")

    response = _send(client, _paid("ivan@example.com"), header="t=1,v1=nope")

    assert response.status_code == 400
    assert client.get("/api/me").json()["plan"] == "free"


def test_cancelled_subscription_returns_to_free_after_the_period(client):
    login(client, "ivan@example.com")
    _send(client, _paid("ivan@example.com"))

    ended = {
        "id": "evt_2",
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "id": "sub_1",
            "status": "canceled",
            # Период кончился минуту назад.
            "current_period_end": int(time.time()) - 60,
        }},
    }
    assert _send(client, ended).status_code == 200

    assert client.get("/api/me").json()["plan"] == "free"


def test_cancelled_subscription_lives_until_the_period_ends(client):
    """Деньги за оплаченный месяц взяты — доступ до его конца остаётся."""
    login(client, "ivan@example.com")
    _send(client, _paid("ivan@example.com"))

    ended = {
        "id": "evt_3",
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "id": "sub_1",
            "status": "canceled",
            "current_period_end": int(time.time()) + 10 * 24 * 3600,
        }},
    }
    _send(client, ended)

    assert client.get("/api/me").json()["plan"] == "pro"


def test_unknown_event_is_not_an_error(client):
    """Stripe шлёт десятки типов; неизвестный — не наше дело, а не сбой."""
    login(client, "ivan@example.com")

    response = _send(client, {"id": "evt_x", "type": "invoice.created",
                              "data": {"object": {}}})

    assert response.status_code == 200


def test_webhook_without_secret_is_503(tmp_path):
    """Биллинг выключен — сервис поднимается и работает на Free."""
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/billing/webhook", content=b"{}")

    assert response.status_code == 503
    assert response.json()["error"] == "billing_not_configured"


# --- создание сессии оплаты -------------------------------------------
#
# Против настоящего Stripe этот путь не проверялся: ключей нет. Тесты
# закрывают всё до отправки и после ответа — что уходит, что возвращается,
# что делаем на сбое.


def test_checkout_needs_a_session(client):
    assert client.post("/api/billing/checkout").status_code == 401


def test_checkout_without_keys_is_503(client):
    login(client, "ivan@example.com")

    response = client.post("/api/billing/checkout")

    assert response.status_code == 503
    assert response.json()["error"] == "billing_not_configured"


def test_checkout_sends_what_stripe_expects():
    from karaoke_api.accounts.billing import create_checkout_session

    sent = {}

    def fake_post(url, data, key):
        sent["url"] = url
        sent["data"] = data
        sent["key"] = key
        return {"url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    url = create_checkout_session(
        "sk_test_x", "price_1", "user-42", "ivan@example.com",
        "https://karaoke.example", post=fake_post,
    )

    assert url.startswith("https://checkout.stripe.com/")
    assert sent["key"] == "sk_test_x"
    assert sent["data"]["mode"] == "subscription"
    assert sent["data"]["line_items[0][price]"] == "price_1"
    assert sent["data"]["customer_email"] == "ivan@example.com"
    # По нему опознаём человека в вебхуке: адрес в Checkout можно поменять.
    assert sent["data"]["client_reference_id"] == "user-42"
    assert sent["data"]["success_url"].startswith("https://karaoke.example")


def test_answer_without_url_is_an_error():
    from karaoke_api.accounts.billing import StripeError, create_checkout_session

    with pytest.raises(StripeError):
        create_checkout_session(
            "sk", "price", "u", "e@x.io", "https://x",
            post=lambda *_: {"error": {"message": "нет такого price"}},
        )


def test_stripe_failure_becomes_502(tmp_path, monkeypatch):
    from karaoke_api.accounts import billing

    def boom(*_args, **_kwargs):
        raise billing.StripeError("сеть недоступна")

    monkeypatch.setattr(billing, "create_checkout_session", boom)

    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        stripe_secret_key="sk_test_x",
        stripe_price_id="price_1",
    )
    with TestClient(create_app(settings)) as c:
        login(c, "ivan@example.com")
        response = c.post("/api/billing/checkout")

    assert response.status_code == 502
    assert response.json()["error"] == "billing_unavailable"
