"""Воркер RunPod: разбор задачи, порядок действий, ответ.

Ни SDK RunPod, ни GPU здесь нет: тело handler'а вынесено в `separate_job`
ровно ради этого. Против настоящего RunPod ничего не выполнялось.
"""

from pathlib import Path

import pytest

from karaoke_api.separation.fake import FakeSeparator
from karaoke_api.worker.handler import BadJob, separate_job


class FakeHttp:
    """Подменяет сеть: GET отдаёт байты, PUT записывает в журнал."""

    def __init__(self, source_bytes: bytes = b"RIFF-fake"):
        self.source_bytes = source_bytes
        self.uploads: list[tuple[str, int]] = []

    def __call__(self, request_or_url, *_args, **_kwargs):
        http = self

        class Response:
            def __init__(self):
                self._left = http.source_bytes

            def read(self, _size=None):
                data, self._left = self._left, b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        if isinstance(request_or_url, str):
            return Response()

        http.uploads.append(
            (request_or_url.full_url, len(request_or_url.data or b""))
        )
        return Response()


def _payload(**overrides):
    payload = {
        "track_id": "t1",
        "source_url": "https://r2.example/source?sig=1",
        "upload_urls": {
            "vocals": "https://r2.example/vocals?sig=2",
            "no_vocals": "https://r2.example/no_vocals?sig=3",
        },
    }
    payload.update(overrides)
    return payload


def test_both_stems_are_uploaded(tmp_path, make_wav):
    http = FakeHttp(Path(make_wav(duration_sec=0.2)).read_bytes())

    result = separate_job(_payload(), FakeSeparator(), tmp_path, opener=http)

    assert result["track_id"] == "t1"
    assert [url for url, _ in http.uploads] == [
        "https://r2.example/vocals?sig=2",
        "https://r2.example/no_vocals?sig=3",
    ]
    # Пустой файл значил бы, что дорожка не записалась, а мы этого не
    # заметили.
    assert all(size > 0 for _, size in http.uploads)


def test_answer_carries_degradation_and_timing(tmp_path, make_wav):
    http = FakeHttp(Path(make_wav(duration_sec=0.2)).read_bytes())

    result = separate_job(_payload(), FakeSeparator(), tmp_path, opener=http)

    assert result["degraded"] is False
    assert result["elapsed_sec"] >= 0


def test_missing_field_is_refused_not_retried(tmp_path):
    """Кривую задачу RunPod повторять не должен: падение вместо ответа
    заставит его биться до исчерпания попыток."""
    with pytest.raises(BadJob) as exc:
        separate_job(_payload(source_url=""), FakeSeparator(), tmp_path,
                     opener=FakeHttp())

    assert "source_url" in str(exc.value)


def test_missing_upload_link_is_refused(tmp_path):
    payload = _payload()
    payload["upload_urls"] = {"vocals": "https://r2.example/v"}

    with pytest.raises(BadJob) as exc:
        separate_job(payload, FakeSeparator(), tmp_path, opener=FakeHttp())

    assert "no_vocals" in str(exc.value)


def test_separator_failure_reaches_runpod(tmp_path, make_wav):
    """Сбой разделения — повод повторить задачу, поэтому он летит наружу, а
    не превращается в успешный ответ с пустыми дорожками."""

    class Broken:
        def separate(self, *_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    http = FakeHttp(Path(make_wav(duration_sec=0.2)).read_bytes())

    with pytest.raises(RuntimeError):
        separate_job(_payload(), Broken(), tmp_path, opener=http)

    assert http.uploads == []


def test_progress_is_passed_through(tmp_path, make_wav):
    seen = []
    http = FakeHttp(Path(make_wav(duration_sec=0.2)).read_bytes())

    separate_job(_payload(), FakeSeparator(), tmp_path, opener=http,
                 on_progress=lambda stage, pct: seen.append(stage))

    assert seen, "этапы должны доходить до вызывающей стороны"
