"""Подписка: проверка подписи вебхука и применение событий Stripe.

Подпись проверяется своим кодом, а не библиотекой `stripe`, по двум причинам.
Во-первых, схема простая и открыто описана: HMAC-SHA256 от строки
`timestamp.payload` с секретом вебхука. Во-вторых — это единственная часть
биллинга, которую можно проверить тестом без ключей и без сети, и тащить ради
неё зависимость незачем.

Состояние подписки берётся **только отсюда**: возврат человека из Checkout
ничего не подтверждает — он мог закрыть вкладку, а платёж мог упасть после
редиректа.
"""

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Сколько терпим расхождение часов между Stripe и нами. Пять минут — их же
# рекомендация: меньше начинает отбрасывать честные доставки.
TOLERANCE_SEC = 300


class BadSignature(Exception):
    pass


def verify_signature(payload: bytes, header: str, secret: str,
                     now: float | None = None) -> None:
    """Бросает `BadSignature`, если заголовку верить нельзя.

    Проверка времени не формальность: без неё подслушанный запрос можно
    переиграть через месяц, и он снова включит подписку.
    """
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        raise BadSignature("заголовок без t или v1")

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise BadSignature("нечисловой timestamp") from exc

    moment = time.time() if now is None else now
    if abs(moment - sent_at) > TOLERANCE_SEC:
        raise BadSignature("слишком старая подпись")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()

    # Сравнение постоянного времени: обычное == утекает побайтно.
    if not hmac.compare_digest(expected, signature):
        raise BadSignature("подпись не сходится")


def _period_end(data: dict) -> datetime | None:
    value = data.get("current_period_end")
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def apply_event(accounts, event: dict) -> bool:
    """Применяет событие к состоянию подписки. True, если что-то изменилось.

    Обрабатываются три вида. Остальные Stripe шлёт десятками, и молча
    отвечать им 200 правильнее, чем падать: неизвестное событие — это не
    ошибка, а просто не наше дело.
    """
    kind = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    if kind == "checkout.session.completed":
        email = (data.get("customer_details") or {}).get("email") \
            or data.get("customer_email")
        if not email:
            log.warning("checkout.session.completed без адреса, пропускаю")
            return False
        user = accounts.user_by_email(email)
        if user is None:
            # Оплата пришла раньше, чем человек завёлся, — такого быть не
            # должно (Checkout открывается из-под сессии), но падать здесь
            # значит просить Stripe повторять доставку вечно.
            log.warning("оплата за неизвестный адрес %s", email)
            return False
        accounts.set_subscription(
            user.id, "pro", "active", _period_end(data),
            data.get("subscription"),
        )
        return True

    if kind in ("customer.subscription.updated",
                "customer.subscription.deleted"):
        subscription_id = data.get("id")
        if not subscription_id:
            return False
        user_id = _user_by_subscription(accounts, subscription_id)
        if user_id is None:
            return False
        status = data.get("status", "canceled")
        if kind == "customer.subscription.deleted":
            status = "canceled"
        accounts.set_subscription(
            user_id,
            "pro" if status in ("active", "trialing", "canceled") else "free",
            status, _period_end(data), subscription_id,
        )
        return True

    return False


def _user_by_subscription(accounts, subscription_id: str) -> str | None:
    with accounts.connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM subscriptions WHERE stripe_subscription_id = ?",
            (subscription_id,),
        ).fetchone()
    return row["user_id"] if row else None
