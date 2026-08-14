"""Объектное хранилище S3-совместимого вида (Cloudflare R2).

Зачем именно R2 — в 4.5 контекста: нулевой egress. На S3 отдача дорожек стоит
столько же, сколько сам GPU, и это не оптимизация, а условие существования
тарифа.

Подпись SigV4 написана здесь, а не взята из boto3. Причина та же, что с
вебхуком Stripe: схема открыто описана, кода на неё меньше, чем весит
зависимость, и её можно проверить тестом без единого сетевого вызова.

ВНИМАНИЕ: против настоящего R2 модуль не выполнялся — ключей в среде
разработки нет. Тесты закрывают подпись, состав запросов и разбор ответов;
принимает ли R2 именно такие запросы, покажет первое живое обращение.
"""

import datetime as dt
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
_UNSIGNED = "UNSIGNED-PAYLOAD"
_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class StorageError(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    k_date = _sign(f"AWS4{secret}".encode("utf-8"), date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, _SERVICE)
    return _sign(k_service, "aws4_request")


def _quote(value: str) -> str:
    # Ключи содержат «/», и экранировать его нельзя: он структурный.
    return urllib.parse.quote(value, safe="/~")


class R2Storage:
    """Реализация протокола `Storage` поверх S3-совместимого API."""

    def __init__(self, endpoint: str, bucket: str, access_key: str,
                 secret_key: str, region: str = "auto",
                 opener=urllib.request.urlopen) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        # Шов для тестов: сеть подменяется целиком, как у Stripe.
        self._opener = opener

    # --- подпись ---------------------------------------------------------

    def _headers(self, method: str, key: str, extra: dict[str, str],
                 now: dt.datetime | None = None) -> dict[str, str]:
        moment = now or dt.datetime.now(dt.timezone.utc)
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date = moment.strftime("%Y%m%d")
        host = urllib.parse.urlparse(self._endpoint).netloc

        headers = {
            "host": host,
            "x-amz-content-sha256": _UNSIGNED,
            "x-amz-date": amz_date,
            **{name.lower(): value for name, value in extra.items()},
        }
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(
            f"{name}:{headers[name]}\n" for name in sorted(headers)
        )

        path, _, query = key.partition("?")
        canonical = "\n".join([
            method,
            f"/{self._bucket}/{_quote(path)}" if path else f"/{self._bucket}",
            query,
            canonical_headers,
            signed,
            _UNSIGNED,
        ])

        scope = f"{date}/{self._region}/{_SERVICE}/aws4_request"
        to_sign = "\n".join(
            [_ALGORITHM, amz_date, scope, _sha256(canonical.encode("utf-8"))]
        )
        signature = hmac.new(
            _signing_key(self._secret_key, date, self._region),
            to_sign.encode("utf-8"), hashlib.sha256,
        ).hexdigest()

        headers["authorization"] = (
            f"{_ALGORITHM} Credential={self._access_key}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        )
        return headers

    def _url(self, key: str) -> str:
        return f"{self._endpoint}/{self._bucket}/{key}" if key else (
            f"{self._endpoint}/{self._bucket}"
        )

    def _request(self, method: str, key: str, body: bytes | None = None,
                 extra: dict[str, str] | None = None) -> bytes:
        request = urllib.request.Request(
            self._url(key), data=body, method=method,
            headers=self._headers(method, key, extra or {}),
        )
        try:
            with self._opener(request) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise StorageError(f"{method} {key}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise StorageError(f"{method} {key}: {exc.reason}") from exc

    # --- интерфейс Storage ----------------------------------------------

    def store_file(self, key: str, src: Path) -> None:
        self._request("PUT", key, body=Path(src).read_bytes())

    def materialize(self, key: str, dest_dir: Path) -> Path:
        dest = Path(dest_dir) / Path(key).name
        dest.write_bytes(self._request("GET", key))
        return dest

    def read_range(self, key: str, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        end = start + length - 1
        return self._request(
            "GET", key, extra={"range": f"bytes={start}-{end}"}
        )

    def iter_range(self, key: str, start: int, length: int,
                   chunk_size: int = 65536) -> Iterator[bytes]:
        # Кусками, а не целиком: дорожка весит десятки мегабайт — та же
        # причина, что и в локальной реализации.
        remaining = length
        position = start
        while remaining > 0:
            chunk = self.read_range(key, position, min(chunk_size, remaining))
            if not chunk:
                return
            yield chunk
            position += len(chunk)
            remaining -= len(chunk)

    def size(self, key: str) -> int:
        request = urllib.request.Request(
            self._url(key), method="HEAD",
            headers=self._headers("HEAD", key, {}),
        )
        try:
            with self._opener(request) as response:
                return int(response.headers["Content-Length"])
        except urllib.error.HTTPError as exc:
            raise StorageError(f"HEAD {key}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise StorageError(f"HEAD {key}: {exc.reason}") from exc

    def exists(self, key: str) -> bool:
        try:
            self.size(key)
        except StorageError:
            return False
        return True

    def list_prefixes(self, prefix: str) -> list[str]:
        normalized = prefix.rstrip("/") + "/"
        query = urllib.parse.urlencode(
            {"list-type": "2", "delimiter": "/", "prefix": normalized}
        )
        root = ET.fromstring(self._request("GET", f"?{query}"))
        out = []
        for node in root.findall("s3:CommonPrefixes/s3:Prefix", _NS):
            name = (node.text or "").removeprefix(normalized).rstrip("/")
            if name:
                out.append(name)
        return out

    def delete_prefix(self, prefix: str) -> None:
        """Удаляет всё под префиксом.

        Каталогов в объектном хранилище нет, поэтому «удалить каталог» — это
        перечислить и удалить по одному. Отсутствие ключей не ошибка: цель в
        том, чтобы после вызова там ничего не было.
        """
        normalized = prefix.rstrip("/") + "/"
        query = urllib.parse.urlencode({"list-type": "2", "prefix": normalized})
        root = ET.fromstring(self._request("GET", f"?{query}"))
        for node in root.findall("s3:Contents/s3:Key", _NS):
            if node.text:
                self._request("DELETE", node.text)

    # --- подписанные ссылки ---------------------------------------------

    def presigned_url(self, key: str, method: str = "GET",
                      expires_sec: int = 300,
                      now: dt.datetime | None = None) -> str:
        """Ссылка для чужого процесса — воркера RunPod.

        Живёт минуты и даёт один ключ, а не бакет: иначе наш GPU по чужой
        просьбе качает что угодно откуда угодно (дизайн serverless, риск
        «ссылки утекли»).
        """
        moment = now or dt.datetime.now(dt.timezone.utc)
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date = moment.strftime("%Y%m%d")
        host = urllib.parse.urlparse(self._endpoint).netloc
        scope = f"{date}/{self._region}/{_SERVICE}/aws4_request"

        params = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self._access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_sec),
            "X-Amz-SignedHeaders": "host",
        }
        query = "&".join(
            f"{urllib.parse.quote(name, safe='')}="
            f"{urllib.parse.quote(value, safe='')}"
            for name, value in sorted(params.items())
        )
        canonical = "\n".join([
            method, f"/{self._bucket}/{_quote(key)}", query,
            f"host:{host}\n", "host", _UNSIGNED,
        ])
        to_sign = "\n".join(
            [_ALGORITHM, amz_date, scope, _sha256(canonical.encode("utf-8"))]
        )
        signature = hmac.new(
            _signing_key(self._secret_key, date, self._region),
            to_sign.encode("utf-8"), hashlib.sha256,
        ).hexdigest()

        return (f"{self._endpoint}/{self._bucket}/{_quote(key)}"
                f"?{query}&X-Amz-Signature={signature}")
