import json
import logging

from karaoke_api.logging_setup import JsonFormatter, configure_logging


def _record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord(
        name="karaoke_api.test", level=logging.INFO, pathname=__file__,
        lineno=1, msg=kwargs.pop("msg", "сообщение"),
        args=kwargs.pop("args", ()), exc_info=kwargs.pop("exc_info", None),
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def test_record_is_valid_json_with_the_basics():
    line = JsonFormatter().format(_record())

    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "karaoke_api.test"
    assert payload["message"] == "сообщение"
    assert payload["ts"].endswith("+00:00")


def test_arguments_are_already_substituted():
    """В поле уходит готовый текст, а не шаблон с процентами."""
    line = JsonFormatter().format(
        _record(msg="трек %s удалён", args=("abc",))
    )

    assert json.loads(line)["message"] == "трек abc удалён"


def test_extra_fields_become_fields():
    # Ради этого всё и затевалось: по track_id можно сгруппировать, по
    # строке текста — нет.
    line = JsonFormatter().format(_record(track_id="abc", elapsed_sec=9.1))

    payload = json.loads(line)
    assert payload["track_id"] == "abc"
    assert payload["elapsed_sec"] == 9.1


def test_exception_is_one_field_not_twenty_lines():
    try:
        raise ValueError("сломалось")
    except ValueError:
        import sys
        line = JsonFormatter().format(_record(exc_info=sys.exc_info()))

    payload = json.loads(line)
    assert "ValueError: сломалось" in payload["exception"]
    assert payload["message"] == "сообщение"


def test_unserializable_value_does_not_break_the_line():
    """Лог не имеет права падать из-за того, что в него положили объект."""
    line = JsonFormatter().format(_record(thing=object()))

    assert "thing" in json.loads(line)


def test_configure_does_not_stack_handlers():
    """Приложение собирается в тестах десятками раз; без уборки каждая запись
    печаталась бы столько раз, сколько было сборок."""
    configure_logging(json_output=True)
    configure_logging(json_output=True)
    configure_logging(json_output=False)

    assert len(logging.getLogger().handlers) == 1
