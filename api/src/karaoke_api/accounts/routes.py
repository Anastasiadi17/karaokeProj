"""Вход по одноразовой ссылке и текущий пользователь.

Почта на этом этапе — строка в логе: провайдер подключается в биллинговом
цикле, а блокироваться на нём здесь нечем. Формат строки один и тот же и для
человека, читающего лог, и для теста, который берёт из неё ссылку.
"""

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import Settings
from .billing import BadSignature, apply_event, verify_signature
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
        # Единственная доставка на этом этапе.
        log.info("ссылка для входа %s: %s", email, link)

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
        }

    return router
