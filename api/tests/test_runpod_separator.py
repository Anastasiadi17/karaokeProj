"""Наша половина serverless: постановка задачи, опрос, забор дорожек.

Сеть и хранилище подменяются целиком — аккаунта RunPod в среде нет. Против
настоящего сервиса ничего не выполнялось.
"""

import json
from pathlib import Path

import pytest

from karaoke_api.separation.runpod_remote import (
    RemoteSeparationError, RunpodSeparator,
)


class FakeStorage:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.stored: list[str] = []
        self.deleted: list[str] = []

    def store_file(self, key, src):
        self.stored.append(key)

    def presigned_url(self, key, method="GET", **_kwargs):
        return f"https://r2.example/{key}?m={method}"

    def materialize(self, key, dest_dir):
        dest = Path(dest_dir) / Path(key).name
        dest.write_bytes(b"wav")
        return dest

    def delete_prefix(self, prefix):
        self.deleted.append(prefix)


def fake_net(statuses, submit=None, log=None):
    """Первый вызов — постановка задачи, дальше опросы по списку статусов."""
    queue = list(statuses)

    class Response:
        def __init__(self, body):
            self._body = json.dumps(body).encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    last = {"value": {"status": "IN_PROGRESS"}}

    def opener(request, *_args, **_kwargs):
        if log is not None:
            log.append(request)
        # Постановка задачи отличается от опроса наличием тела.
        if request.data is not None:
            return Response(submit if submit is not None else {"id": "job-1"})
        # Кончился сценарий — повторяем последний ответ, а не подсовываем
        # успех: иначе тест на таймаут зеленел бы сам собой.
        if queue:
            last["value"] = queue.pop(0)
        return Response(last["value"])

    return opener


def separator(tmp_path, statuses, submit=None, log=None, **kwargs):
    return RunpodSeparator(
        "https://api.runpod.ai/v2/xyz", "key", FakeStorage(tmp_path),
        opener=fake_net(statuses, submit, log), sleep=lambda _s: None,
        **kwargs,
    )


def test_returns_both_stems(tmp_path, make_wav):
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [{"status": "COMPLETED", "output": {}}])

    result = sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()
    assert result.degraded is False


def test_degradation_reaches_the_result(tmp_path, make_wav):
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(
        tmp_path, [{"status": "COMPLETED", "output": {"degraded": True}}]
    )

    result = sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)

    assert result.degraded is True


def test_job_carries_signed_links(tmp_path, make_wav):
    log = []
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [{"status": "COMPLETED", "output": {}}], log=log)

    sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)

    payload = json.loads(log[0].data.decode("utf-8"))["input"]
    assert payload["source_url"].startswith("https://r2.example/work/")
    assert payload["upload_urls"]["vocals"].endswith("m=PUT")
    assert payload["upload_urls"]["no_vocals"].endswith("m=PUT")


def test_stages_reach_the_caller(tmp_path, make_wav):
    """Экран не должен выглядеть замершим, пока задача стоит в их очереди."""
    seen = []
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [
        {"status": "IN_QUEUE"},
        {"status": "IN_PROGRESS"},
        {"status": "COMPLETED", "output": {}},
    ])

    sep.separate(make_wav(duration_sec=0.2), out,
                 lambda stage, pct: seen.append(stage))

    assert seen == ["loading", "separating", "writing"]


def test_temporary_source_is_cleaned_up(tmp_path, make_wav):
    out = tmp_path / "out"
    out.mkdir()
    storage = FakeStorage(tmp_path)
    sep = RunpodSeparator(
        "https://api.runpod.ai/v2/xyz", "key", storage,
        opener=fake_net([{"status": "COMPLETED", "output": {}}]),
        sleep=lambda _s: None,
    )

    sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)

    # Исходник в хранилище нужен был только воркеру: платить за него сутки
    # хранения незачем.
    assert storage.deleted and storage.deleted[0].startswith("work/")


def test_failed_job_is_an_error(tmp_path, make_wav):
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [{"status": "FAILED", "error": "OOM"}])

    with pytest.raises(RemoteSeparationError):
        sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)


def test_answer_without_id_is_an_error(tmp_path, make_wav):
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [], submit={"error": "нет такого эндпоинта"})

    with pytest.raises(RemoteSeparationError):
        sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)


def test_endless_job_stops_by_timeout(tmp_path, make_wav):
    """Иначе задача, зависшая у них, держит нашего воркера навсегда."""
    out = tmp_path / "out"
    out.mkdir()
    sep = separator(tmp_path, [{"status": "IN_PROGRESS"}] * 50,
                    timeout_sec=0.0)

    with pytest.raises(RemoteSeparationError) as exc:
        sep.separate(make_wav(duration_sec=0.2), out, lambda *_: None)

    assert "не завершилась" in str(exc.value)
