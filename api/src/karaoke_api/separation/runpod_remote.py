"""Разделение на стороне RunPod Serverless.

Наша половина той же пары, что и `worker/handler.py`: ставит задачу, ждёт,
забирает дорожки. Реализует тот же протокол `StemSeparator`, что demucs и
подделка, — поэтому переключение делается настройкой, а всё остальное в
сервисе не двигается (дизайн serverless, решение 1).

Асинхронный вызов, а не `runsync`: трек в предельные 600 с — это ~21 с работы
плюс возможные ~20 с холодного старта, и держать HTTP-соединение всё это время
значит зависеть от каждого прокси по пути.

ВНИМАНИЕ: против настоящего RunPod не выполнялось — аккаунта в среде
разработки нет. Сеть подменяется в тестах целиком; примет ли RunPod такие
запросы, покажет первый живой вызов.
"""

import json
import logging
import time
import urllib.request
from pathlib import Path

from .base import SeparationResult

log = logging.getLogger(__name__)

# Их статусы в наши этапы. `IN_QUEUE` — это ещё не работа, но человеку надо
# показывать хоть что-то, иначе экран выглядит замершим.
_STAGES = {
    "IN_QUEUE": ("loading", 0.05),
    "IN_PROGRESS": ("separating", 0.5),
    "COMPLETED": ("writing", 0.95),
}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class RemoteSeparationError(RuntimeError):
    pass


def _post(url: str, payload: dict, api_key: str,
          opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"authorization": f"Bearer {api_key}",
                 "content-type": "application/json"},
    )
    with opener(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, api_key: str, opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        url, headers={"authorization": f"Bearer {api_key}"}
    )
    with opener(request) as response:
        return json.loads(response.read().decode("utf-8"))


class RunpodSeparator:
    def __init__(self, endpoint: str, api_key: str, storage,
                 poll_sec: float = 2.0, timeout_sec: float = 900.0,
                 opener=urllib.request.urlopen, sleep=time.sleep) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._storage = storage
        self._poll = poll_sec
        self._timeout = timeout_sec
        self._opener = opener
        self._sleep = sleep

    def warmup(self) -> None:
        """Греть нечего: модель живёт в чужом контейнере и прогревается там,
        до объявления готовности воркера."""

    def separate(self, source: Path, out_dir: Path, on_progress
                 ) -> SeparationResult:
        track_id = Path(source).parent.name or "job"
        # Исходник кладётся в хранилище: воркер живёт на другой машине, и
        # локальный путь ему ничего не говорит. С R2 это лишний круг —
        # исходник там уже есть, — и он уйдёт, когда протокол `StemSeparator`
        # научится принимать ключ вместо пути. Сейчас честнее переплатить
        # одной загрузкой, чем ломать интерфейс, на котором держатся demucs и
        # подделка.
        source_key = f"work/{track_id}/source{Path(source).suffix}"
        self._storage.store_file(source_key, Path(source))

        payload = {"input": {
            "track_id": track_id,
            "source_url": self._storage.presigned_url(source_key, "GET"),
            "upload_urls": {
                kind: self._storage.presigned_url(
                    f"tracks/{track_id}/stems/{kind}.wav", "PUT"
                )
                for kind in ("vocals", "no_vocals")
            },
        }}

        submitted = _post(f"{self._endpoint}/run", payload, self._api_key,
                          self._opener)
        job_id = submitted.get("id")
        if not job_id:
            raise RemoteSeparationError(f"ответ без id: {str(submitted)[:200]}")

        output = self._wait(job_id, on_progress)

        # Дорожки лежат в хранилище — забираем их туда, где их ждёт раннер.
        out = Path(out_dir)
        vocals = self._storage.materialize(
            f"tracks/{track_id}/stems/vocals.wav", out)
        no_vocals = self._storage.materialize(
            f"tracks/{track_id}/stems/no_vocals.wav", out)
        self._storage.delete_prefix(f"work/{track_id}")

        return SeparationResult(
            vocals=vocals, no_vocals=no_vocals,
            degraded=bool(output.get("degraded", False)),
        )

    def _wait(self, job_id: str, on_progress) -> dict:
        deadline = time.monotonic() + self._timeout
        last_stage = None

        while True:
            state = _get(f"{self._endpoint}/status/{job_id}", self._api_key,
                         self._opener)
            status = state.get("status", "")

            stage = _STAGES.get(status)
            if stage and stage[0] != last_stage:
                on_progress(stage[0], stage[1])
                last_stage = stage[0]

            if status == "COMPLETED":
                return state.get("output") or {}
            if status in _TERMINAL:
                # Их текст ошибки уходит в лог целиком, наружу — короткий
                # факт: чужие сообщения человеку ничего не объясняют.
                log.error("RunPod вернул %s: %s", status,
                          str(state.get("error"))[:500])
                raise RemoteSeparationError(f"задача завершилась как {status}")

            if time.monotonic() >= deadline:
                raise RemoteSeparationError(
                    f"задача не завершилась за {self._timeout} с"
                )
            self._sleep(self._poll)
