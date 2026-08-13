"""Кредиты: журнал начислений и списаний.

Тратить их пока не на что — первой AI-функции не существует. Здесь
проверяется само хранилище и покупка пакета: баланс купленного должен быть
виден сразу, а не появиться вместе с функцией.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_billing import SECRET, _sign
from karaoke_api.accounts.store import AccountStore
from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def store(tmp_path):
    s = AccountStore(tmp_path / "db.sqlite")
    yield s
    s.close()


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


def test_balance_is_the_sum_of_the_ledger(store):
    user = store.upsert_user("ivan@example.com")

    store.add_credits(user.id, 100, "purchase")
    store.add_credits(user.id, 20, "bonus")
    store.spend_credits(user.id, 30, "ai_fill")

    assert store.credit_balance(user.id) == 90


def test_new_user_starts_at_zero(store):
    user = store.upsert_user("ivan@example.com")

    assert store.credit_balance(user.id) == 0


def test_spending_more_than_there_is_changes_nothing(store):
    user = store.upsert_user("ivan@example.com")
    store.add_credits(user.id, 5, "purchase")

    assert store.spend_credits(user.id, 6, "ai_fill") is False
    assert store.credit_balance(user.id) == 5


def test_spending_exactly_the_balance_works(store):
    user = store.upsert_user("ivan@example.com")
    store.add_credits(user.id, 5, "purchase")

    assert store.spend_credits(user.id, 5, "ai_fill") is True
    assert store.credit_balance(user.id) == 0


def test_negative_spend_is_a_mistake_not_a_gift(store):
    user = store.upsert_user("ivan@example.com")

    with pytest.raises(ValueError):
        store.spend_credits(user.id, -10, "хитрость")


def test_repeated_stripe_event_does_not_double_the_credits(store):
    user = store.upsert_user("ivan@example.com")

    assert store.add_credits(user.id, 100, "purchase", "evt_1") is True
    assert store.add_credits(user.id, 100, "purchase", "evt_1") is False

    assert store.credit_balance(user.id) == 100


def test_credits_are_counted_per_user(store):
    ivan = store.upsert_user("ivan@example.com")
    petr = store.upsert_user("petr@example.com")
    store.add_credits(ivan.id, 100, "purchase")

    assert store.credit_balance(petr.id) == 0


def test_purchase_through_the_webhook_shows_up_in_me(client):
    login(client, "ivan@example.com")
    assert client.get("/api/me").json()["credits"] == 0

    event = {
        "id": "evt_credits_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "payment",
            "customer_details": {"email": "ivan@example.com"},
            "metadata": {"credits": "100"},
        }},
    }
    payload = json.dumps(event).encode("utf-8")
    response = client.post(
        "/api/billing/webhook", content=payload,
        headers={"stripe-signature": _sign(payload)},
    )

    assert response.status_code == 200
    assert client.get("/api/me").json()["credits"] == 100


def test_subscription_payment_does_not_grant_credits(client):
    """Подписка и кредиты — разные покупки, и путать их нельзя."""
    login(client, "ivan@example.com")
    event = {
        "id": "evt_sub_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "mode": "subscription",
            "customer_details": {"email": "ivan@example.com"},
            "subscription": "sub_1",
            "current_period_end": int(time.time()) + 30 * 24 * 3600,
        }},
    }
    payload = json.dumps(event).encode("utf-8")
    client.post("/api/billing/webhook", content=payload,
                headers={"stripe-signature": _sign(payload)})

    body = client.get("/api/me").json()
    assert body["plan"] == "pro"
    assert body["credits"] == 0
