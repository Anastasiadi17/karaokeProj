"""Отправка письма со ссылкой входа.

Без настроек SMTP письмо уходит в лог — это рабочий режим разработки, в
котором проект поднимается без единого чужого аккаунта. Как только задан
`KARAOKE_SMTP_HOST`, та же ссылка уходит настоящим письмом.

ВНИМАНИЕ: путь через SMTP против живого сервера не выполнялся — почтового
аккаунта в среде разработки нет. Тесты закрывают выбор ветки, состав письма и
поведение при сбое; принимает ли конкретный провайдер именно такой конверт,
покажет первое настоящее письмо.
"""

import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def build_message(sender: str, to: str, link: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Вход в караоке-студию"
    message["From"] = sender
    message["To"] = to
    # Без темы «подтвердите» и кнопок: письмо с одной ссылкой реже уезжает в
    # спам и не похоже на фишинг, которым притворяется половина рассылок.
    message.set_content(
        "Ссылка для входа:\n\n"
        f"{link}\n\n"
        "Она действует 15 минут и срабатывает один раз.\n"
        "Если вход запрашивали не вы — письмо можно удалить."
    )
    return message


def _send_smtp(settings, message: EmailMessage) -> None:
    """Отдельной функцией ради шва: тест подменяет её целиком."""
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        if settings.smtp_tls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(message)


def send_login_link(settings, email: str, link: str, send=_send_smtp) -> None:
    """Никогда не бросает наружу.

    Ответ эндпоинта одинаков для любого адреса, и сбой доставки не должен
    этого менять: иначе по коду ответа станет видно, кому письмо ушло, а
    кому нет. Сбой уходит в лог, где его увидит тот, кто чинит.
    """
    if not settings.smtp_host:
        log.info("ссылка для входа %s: %s", email, link)
        return

    try:
        send(settings, build_message(settings.smtp_from or settings.smtp_user,
                                     email, link))
    except Exception:
        log.exception("не удалось отправить письмо на %s", email)
