from datetime import datetime, timedelta, timezone

import pytest

from karaoke_api.accounts.store import AccountStore


@pytest.fixture
def store(tmp_path):
    s = AccountStore(tmp_path / "db.sqlite")
    yield s
    s.close()


def _now():
    return datetime.now(timezone.utc)


# --- пользователи ------------------------------------------------------


def test_user_is_created_once_per_email(store):
    first = store.upsert_user("ivan@example.com")
    second = store.upsert_user("ivan@example.com")

    assert first.id == second.id


def test_email_case_and_spaces_do_not_split_the_person(store):
    """Человек, написавший адрес с заглавной, — тот же человек.

    Иначе у него окажется два аккаунта с разными лимитами, и второй он
    заведёт, сам того не заметив.
    """
    first = store.upsert_user("  Ivan@Example.COM ")
    second = store.upsert_user("ivan@example.com")

    assert first.id == second.id
    assert first.email == "ivan@example.com"


# --- одноразовые ссылки ------------------------------------------------


def test_raw_login_token_is_not_stored(store, tmp_path):
    """В базе лежит хеш: утечка файла не должна давать вход."""
    raw = store.create_login_token("ivan@example.com")

    with store.connection() as conn:
        rows = conn.execute("SELECT * FROM login_tokens").fetchall()

    assert len(rows) == 1
    assert raw not in str(tuple(rows[0]))


def test_login_token_works_once(store):
    raw = store.create_login_token("ivan@example.com")

    assert store.consume_login_token(raw) is not None
    assert store.consume_login_token(raw) is None


def test_login_token_creates_the_user(store):
    raw = store.create_login_token("new@example.com")

    user = store.consume_login_token(raw)

    assert user is not None
    assert user.email == "new@example.com"
    assert store.get_user(user.id) is not None


def test_expired_login_token_does_not_work(store):
    raw = store.create_login_token("ivan@example.com", ttl=timedelta(seconds=-1))

    assert store.consume_login_token(raw) is None


def test_unknown_login_token_is_not_an_error(store):
    assert store.consume_login_token("такого-не-выдавали") is None


# --- сессии ------------------------------------------------------------


def test_session_returns_its_user(store):
    user = store.upsert_user("ivan@example.com")

    token = store.create_session(user.id)

    found = store.user_for_session(token)
    assert found is not None
    assert found.id == user.id


def test_foreign_session_token_returns_nothing(store):
    store.upsert_user("ivan@example.com")

    assert store.user_for_session("чужое") is None


def test_expired_session_returns_nothing(store):
    user = store.upsert_user("ivan@example.com")

    token = store.create_session(user.id, ttl=timedelta(seconds=-1))

    assert store.user_for_session(token) is None


def test_delete_session_logs_out(store):
    user = store.upsert_user("ivan@example.com")
    token = store.create_session(user.id)

    store.delete_session(token)

    assert store.user_for_session(token) is None


# --- операции ----------------------------------------------------------


def test_operations_are_counted_per_user_and_period(store):
    ivan = store.upsert_user("ivan@example.com")
    petr = store.upsert_user("petr@example.com")

    store.record_operation(ivan.id, "separate", "t1")
    store.record_operation(ivan.id, "separate", "t2")
    store.record_operation(petr.id, "separate", "t3")

    since = _now() - timedelta(hours=1)
    assert store.count_operations(ivan.id, since) == 2
    assert store.count_operations(petr.id, since) == 1


def test_operations_before_the_period_do_not_count(store):
    """Лимит обнуляется первого числа — старое не должно тянуться."""
    user = store.upsert_user("ivan@example.com")
    store.record_operation(user.id, "separate", "t1")

    since = _now() + timedelta(seconds=1)

    assert store.count_operations(user.id, since) == 0
