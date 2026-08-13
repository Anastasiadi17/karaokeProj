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

    if kind == "checkout.session.completed" and data.get("mode") == "payment":
        # Разовая покупка пакета кредитов. Сколько именно — кладём в
        # metadata при создании сессии: цена и объём пакета живут у Stripe,
        # и дублировать их у себя значит однажды разойтись.
        email = (data.get("customer_details") or {}).get("email")             or data.get("customer_email")
        amount = (data.get("metadata") or {}).get("credits")
        if not email or not amount:
            log.warning("покупка кредитов без адреса или количества")
            return False
        user = accounts.user_by_email(email)
        if user is None:
            log.warning("покупка кредитов за неизвестный адрес %s", email)
            return False
        return accounts.add_credits(
            user.id, int(amount), "purchase", event.get("id"),
        )

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
            # Идентификатор клиента нужен порталу: без него человеку негде
            # отменить подписку и поменять карту.
            data.get("customer"),
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


# --- создание сессии оплаты ------------------------------------------
#
# ВНИМАНИЕ: этот кусок НИКОГДА не выполнялся против настоящего Stripe —
# ключей и сети к ним в среде разработки нет. Тесты проверяют всё до
# отправки (какие поля уходят, что возвращается, как ведём себя на ошибке),
# но принимает ли Stripe именно такой запрос, знает только первый живой
# вызов. Что проверить с тестовым ключом:
#   1. Форма `line_items[0][price]` — Stripe ждёт именно скобочный синтаксис.
#   2. `mode=subscription` требует, чтобы price был recurring.
#   3. `client_reference_id` возвращается в вебхуке — на него можно
#      опереться вместо адреса почты, если адрес в Checkout поменяли.

STRIPE_API = "https://api.stripe.com/v1/checkout/sessions"


class StripeError(Exception):
    pass


def _post_form(url: str, data: dict[str, str], secret_key: str) -> dict:
    """Отдельной функцией, чтобы тест мог подменить её целиком.

    Библиотека `stripe` сюда не тащится: один POST формой не стоит ещё одной
    зависимости в образе, который и так весит гигабайты из-за торча.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return _json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise StripeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise StripeError(f"сеть: {exc.reason}") from exc


def create_checkout_session(secret_key: str, price_id: str, user_id: str,
                            email: str, base_url: str,
                            post=_post_form) -> str:
    """Возвращает адрес, куда отправить человека платить."""
    data = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        # Адрес подставляется, но человек может его в Checkout поменять,
        # поэтому опознаём по client_reference_id — он приходит в вебхуке.
        "customer_email": email,
        "client_reference_id": user_id,
        "success_url": f"{base_url}/?paid=1",
        "cancel_url": f"{base_url}/",
    }
    body = post(STRIPE_API, data, secret_key)
    url = body.get("url")
    if not url:
        raise StripeError(f"ответ без url: {str(body)[:200]}")
    return url


PORTAL_API = "https://api.stripe.com/v1/billing_portal/sessions"


def create_portal_session(secret_key: str, customer_id: str, base_url: str,
                          post=_post_form) -> str:
    """Адрес портала Stripe: отмена подписки, смена карты, счета.

    Своего экрана управления подпиской нет намеренно: всё, что там нужно,
    Stripe уже умеет, а нам это стоило бы обработки отмен, пропорций возврата
    и хранения истории платежей.

    Против живого Stripe не выполнялось, как и Checkout.
    """
    body = post(PORTAL_API,
                {"customer": customer_id, "return_url": f"{base_url}/"},
                secret_key)
    url = body.get("url")
    if not url:
        raise StripeError(f"портал без url: {str(body)[:200]}")
    return url
