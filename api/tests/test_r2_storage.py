"""R2Storage: подпись, состав запросов, разбор ответов.

Сеть подменяется целиком — ключей от настоящего R2 в среде нет. Поэтому
проверяется всё до отправки и после ответа; принимает ли R2 такие запросы,
покажет первое живое обращение.
"""

import datetime as dt
import urllib.error
from io import BytesIO

import pytest

from karaoke_api.storage.r2 import R2Storage, StorageError

MOMENT = dt.datetime(2026, 8, 14, 12, 0, 0, tzinfo=dt.timezone.utc)

LIST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <CommonPrefixes><Prefix>tracks/abc/</Prefix></CommonPrefixes>
  <CommonPrefixes><Prefix>tracks/def/</Prefix></CommonPrefixes>
  <Contents><Key>tracks/abc/original.mp3</Key></Contents>
  <Contents><Key>tracks/abc/stems/vocals.wav</Key></Contents>
</ListBucketResult>"""


class FakeResponse:
    def __init__(self, body=b"", headers=None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def storage(responses=None, log=None):
    queue = list(responses or [FakeResponse()])

    def opener(request, *_args, **_kwargs):
        if log is not None:
            log.append(request)
        return queue.pop(0) if queue else FakeResponse()

    return R2Storage("https://acc.r2.cloudflarestorage.com", "karaoke",
                     "AKIA_TEST", "secret", opener=opener)


# --- подпись -----------------------------------------------------------


def test_signature_is_deterministic():
    first = storage()._headers("GET", "tracks/a.mp3", {}, now=MOMENT)
    second = storage()._headers("GET", "tracks/a.mp3", {}, now=MOMENT)

    assert first["authorization"] == second["authorization"]


def test_signature_changes_with_the_key():
    one = storage()._headers("GET", "tracks/a.mp3", {}, now=MOMENT)
    two = storage()._headers("GET", "tracks/b.mp3", {}, now=MOMENT)

    assert one["authorization"] != two["authorization"]


def test_signature_covers_extra_headers():
    """Range обязан входить в подпись: иначе его можно подменить по дороге."""
    plain = storage()._headers("GET", "k", {}, now=MOMENT)
    ranged = storage()._headers("GET", "k", {"range": "bytes=0-1"}, now=MOMENT)

    assert "range" in ranged["authorization"]
    assert plain["authorization"] != ranged["authorization"]


def test_header_carries_credentials_and_scope():
    headers = storage()._headers("PUT", "k", {}, now=MOMENT)

    assert "Credential=AKIA_TEST/20260814/auto/s3/aws4_request" in \
        headers["authorization"]
    assert headers["x-amz-date"] == "20260814T120000Z"


# --- запросы -----------------------------------------------------------


def test_store_file_puts_the_bytes(tmp_path):
    log = []
    source = tmp_path / "a.wav"
    source.write_bytes(b"12345")

    storage(log=log).store_file("tracks/a/original.wav", source)

    assert log[0].method == "PUT"
    assert log[0].data == b"12345"
    assert log[0].full_url.endswith("/karaoke/tracks/a/original.wav")


def test_read_range_asks_for_the_range():
    log = []

    storage([FakeResponse(b"xyz")], log=log).read_range("k", 10, 3)

    assert log[0].get_header("Range") == "bytes=10-12"


def test_empty_range_does_not_go_to_the_network():
    log = []

    assert storage(log=log).read_range("k", 0, 0) == b""
    assert log == []


def test_iter_range_walks_in_chunks():
    responses = [FakeResponse(b"ab"), FakeResponse(b"cd"), FakeResponse(b"e")]

    chunks = list(storage(responses).iter_range("k", 0, 5, chunk_size=2))

    assert b"".join(chunks) == b"abcde"


def test_size_reads_the_header():
    store = storage([FakeResponse(headers={"Content-Length": "4096"})])

    assert store.size("k") == 4096


def test_missing_key_does_not_exist():
    def opener(*_args, **_kwargs):
        raise urllib.error.HTTPError("u", 404, "no", {}, BytesIO(b""))

    store = R2Storage("https://x", "b", "k", "s", opener=opener)

    assert store.exists("нет такого") is False


def test_network_failure_is_a_storage_error():
    def opener(*_args, **_kwargs):
        raise urllib.error.URLError("сеть недоступна")

    store = R2Storage("https://x", "b", "k", "s", opener=opener)

    with pytest.raises(StorageError):
        store._request("GET", "k")


# --- перечисление и удаление -------------------------------------------


def test_list_prefixes_returns_ids_without_the_prefix():
    result = storage([FakeResponse(LIST_XML)]).list_prefixes("tracks")

    assert result == ["abc", "def"]


def test_delete_prefix_removes_every_key():
    log = []
    store = storage([FakeResponse(LIST_XML), FakeResponse(), FakeResponse()],
                    log=log)

    store.delete_prefix("tracks/abc")

    deleted = [r.full_url for r in log if r.method == "DELETE"]
    assert len(deleted) == 2
    assert deleted[0].endswith("/karaoke/tracks/abc/original.mp3")


# --- подписанные ссылки ------------------------------------------------


def test_presigned_url_carries_everything_needed():
    url = storage().presigned_url("tracks/a/original.wav", now=MOMENT)

    assert url.startswith(
        "https://acc.r2.cloudflarestorage.com/karaoke/tracks/a/original.wav?"
    )
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Expires=300" in url
    assert "X-Amz-Signature=" in url


def test_presigned_url_is_bound_to_the_key():
    """Ссылка даёт один ключ, а не бакет: иначе воркер качает что угодно."""
    one = storage().presigned_url("a", now=MOMENT)
    two = storage().presigned_url("b", now=MOMENT)

    assert one.split("X-Amz-Signature=")[1] != two.split("X-Amz-Signature=")[1]


def test_presigned_url_differs_for_upload_and_download():
    get = storage().presigned_url("k", method="GET", now=MOMENT)
    put = storage().presigned_url("k", method="PUT", now=MOMENT)

    assert get != put
