"""Логи, пригодные для чтения машиной.

Пока сервис на одной машине под присмотром, текстовый лог удобнее: его читают
глазами. Как только он уедет на сервер, глаза заменяются поиском по полям, и
строка «не удалось удалить трек 1098ae93…» перестаёт быть строкой — она
становится записью с полем `track_id`, по которому можно сгруппировать.

Поэтому формат переключаемый, а не выбранный раз навсегда: `KARAOKE_LOG_JSON`
включает JSON, по умолчанию остаётся человеческий текст.
"""

import json
import logging
from datetime import datetime, timezone

# Поля, которые есть у любой записи logging и не несут ничего сверх
# перечисленного ниже. Всё, что не отсюда, попадёт в JSON как есть — так
# `log.info("...", extra={"track_id": ...})` начинает работать сам собой.
_STANDARD = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            # Traceback остаётся одной строкой внутри поля: разорванный по
            # строкам, он в любом сборщике логов превращается в двадцать
            # несвязанных записей.
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(json_output: bool, level: str = "INFO") -> None:
    """Ставит формат корневому логгеру. Вызывается один раз при сборке app."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    # Свои обработчики убираются: иначе при повторной сборке приложения
    # (тесты создают его десятками) каждая запись печатается заново столько
    # раз, сколько было сборок.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
