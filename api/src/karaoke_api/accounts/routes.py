"""Вход по одноразовой ссылке и текущий пользователь.

Почта на этом этапе — строка в логе: провайдер подключается в биллинговом
цикле, а блокироваться на нём здесь нечем. Формат строки один и тот же и для
человека, читающего лог, и для теста, который берёт из неё ссылку.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import Settings
from . import billing
from .billing import BadSignature, StripeError, apply_event, verify_signature
from .mail import send_login_link
from .store import User, normalize_email

log = logging.getLogger(__name__)

SESSION_COOKIE = "karaoke_session"

# Не валидатор адресов, а отсев очевидного мусора: настоящую проверку делает
# само письмо — дошло, значит адрес существует.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def month_start(now: datetime | None = None) -> datetime:
    """Начало календарного месяца в UTC — граница лимита Free.

    Календарный месяц, а не скользящие 30 дней: человек должен уметь ответить
    себе «когда мне снова можно», не открывая калькулятор.
    """
    moment = now or datetime.now(timezone.utc)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def current_user(request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return request.app.state.karaoke.accounts.user_for_session(token)


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/request")
    async def request_link(request: Request):
        body = await request.json()
        email = normalize_email(str(body.get("email", "")))
        if not _EMAIL.match(email):
            return _error("invalid_email")

        accounts = request.app.state.karaoke.accounts
        raw = accounts.create_login_token(email)
        link = f"{settings.public_base_url}/api/auth/callback?token={raw}"
        send_login_link(settings, email, link)

        if settings.expose_login_link:
            # Только с явно включённым флагом — см. config.py.
            return JSONResponse({"link": link})

        # Ответ одинаков для существующего и несуществующего адреса: иначе
        # эндпоинт превращается в проверялку «есть ли такой пользователь» и
        # раздаёт список клиентов любому желающему.
        return Response(status_code=204)

    @router.get("/api/auth/callback")
    async def callback(request: Request, token: str = ""):
        accounts = request.app.state.karaoke.accounts
        user = accounts.consume_login_token(token)
        if user is None:
            return _error("invalid_token")

        session = accounts.create_session(user.id)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            session,
            max_age=settings.session_ttl_days * 24 * 3600,
            httponly=True,
            samesite="lax",
            # Secure только там, где сервис действительно за https: на
            # локальном http кука с этим флагом просто не сохранится, и вход
            # сломается молча.
            secure=settings.public_base_url.startswith("https://"),
        )
        return response

    @router.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            request.app.state.karaoke.accounts.delete_session(token)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @router.post("/api/billing/checkout")
    async def start_checkout(request: Request):
        user = current_user(request)
        if user is None:
            return _error("unauthorized", status=401)

        if not settings.stripe_secret_key or not settings.stripe_price_id:
            return _error("billing_not_configured", status=503)

        try:
            # В отдельный поток: обращение к чужому серверу не должно
            # вставать поперёк событийного цикла.
            url = await asyncio.to_thread(
                # Через модуль, а не по имени: иначе подмена в тесте не
                # доходит до вызова, и тест «сбой Stripe даёт 502» зеленеет
                # по случайности — на настоящей ошибке сети.
                billing.create_checkout_session,
                settings.stripe_secret_key, settings.stripe_price_id,
                user.id, user.email, settings.public_base_url,
            )
        except StripeError:
            # Подробности — в лог, наружу только факт: тексты чужого API
            # человеку ничего не объясняют.
            log.exception("Stripe не отдал сессию оплаты")
            return _error("billing_unavailable", status=502)

        return {"url": url}

    @router.post("/api/billing/portal")
    async def open_portal(request: Request):
        user = current_user(request)
        if user is None:
            return _error("unauthorized", status=401)
        if not settings.stripe_secret_key:
            return _error("billing_not_configured", status=503)

        accounts = request.app.state.karaoke.accounts
        customer = accounts.customer_id(user.id)
        if customer is None:
            # Портал открывать нечего: подписки не было ни разу.
            return _error("no_subscription", status=409)

        try:
            url = await asyncio.to_thread(
                billing.create_portal_session,
                settings.stripe_secret_key, customer,
                settings.public_base_url,
            )
        except StripeError:
            log.exception("Stripe не отдал портал")
            return _error("billing_unavailable", status=502)
        return {"url": url}

    @router.post("/api/billing/webhook")
    async def stripe_webhook(request: Request):
        """Единственный источник правды о подписке."""
        if not settings.stripe_webhook_secret:
            return _error("billing_not_configured", status=503)

        payload = await request.body()
        try:
            verify_signature(payload, request.headers.get("stripe-signature", ""),
                             settings.stripe_webhook_secret)
        except BadSignature as exc:
            # 400, а не 401: для Stripe это сигнал не повторять доставку.
            log.warning("вебхук с плохой подписью: %s", exc)
            return _error("bad_signature", status=400)

        event = json.loads(payload.decode("utf-8"))
        event_id = event.get("id")
        accounts = request.app.state.karaoke.accounts

        # Идемпотентность: Stripe доставляет повторно после любого нашего
        # таймаута, и второй раз включать подписку нельзя.
        if event_id and not accounts.remember_event(event_id):
            return Response(status_code=200)

        apply_event(accounts, event)
        return Response(status_code=200)

    @router.get("/api/me")
    async def me(request: Request):
        user = current_user(request)
        if user is None:
            return _error("unauthorized", status=401)

        accounts = request.app.state.karaoke.accounts
        used = accounts.count_operations(user.id, month_start())
        return {
            "email": user.email,
            "plan": accounts.plan_for(user.id),
            "operations_used": used,
            "operations_limit": settings.free_monthly_operations,
            # Тратить кредиты пока не на что: первой AI-функции нет. Поле
            # существует, чтобы баланс купленного пакета был виден сразу,
            # а не появился вместе с функцией.
            "credits": accounts.credit_balance(user.id),
        }

    return router
