"""Точка входа воркера RunPod Serverless.

Делает одно: забирает исходник по подписанной ссылке, гоняет тот же
`DemucsSeparator`, что и локальный запуск, кладёт дорожки по подписанным
ссылкам на запись. Ни базы, ни очереди, ни знания о пользователях — всё это
остаётся у всегда включённого API (дизайн serverless, решение 1).

Ссылки приходят готовыми и не строятся здесь. Воркер не должен уметь
обращаться к произвольному адресу: подписывает их наша сторона, они живут
минуты и дают один ключ. Иначе наш GPU по чужой просьбе качает что угодно
откуда угодно, и платим за это мы.

ВНИМАНИЕ: против настоящего RunPod не выполнялось — ни аккаунта, ни GPU-пула
в среде разработки нет. Тесты закрывают разбор задачи, порядок действий и
ответ; примет ли RunPod такой handler, покажет первый живой запуск.
"""

import logging
import tempfile
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# Ключи внутри задачи. Имена — часть контракта с нашей стороной
# (`RunpodSeparator`), и менять их можно только вместе с ней.
REQUIRED = ("track_id", "source_url", "upload_urls")


class BadJob(Exception):
    """Задача пришла кривой. Повторять её бессмысленно — отвечаем ошибкой."""


def _download(url: str, dest: Path, opener=urllib.request.urlopen) -> Path:
    with opener(url) as response, dest.open("wb") as out:
        # Кусками: трек весит до 200 МиБ, и целиком в память его тянуть
        # незачем — воркеру эта память нужна под саму модель.
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    return dest


def _upload(url: str, src: Path, opener=urllib.request.urlopen) -> None:
    request = urllib.request.Request(
        url, data=src.read_bytes(), method="PUT",
        headers={"content-type": "audio/wav"},
    )
    with opener(request):
        pass


def separate_job(payload: dict, separator, workdir: Path,
                 opener=urllib.request.urlopen,
                 on_progress=None) -> dict:
    """Тело handler'а без единой зависимости от RunPod.

    Вынесено отдельно ровно ради этого: так весь порядок действий
    проверяется тестом, не поднимая ни их SDK, ни GPU.
    """
    missing = [name for name in REQUIRED if not payload.get(name)]
    if missing:
        raise BadJob(f"в задаче нет полей: {', '.join(missing)}")

    uploads = payload["upload_urls"]
    for kind in ("vocals", "no_vocals"):
        if not uploads.get(kind):
            raise BadJob(f"нет ссылки для загрузки дорожки {kind}")

    started = time.perf_counter()
    source = _download(payload["source_url"], workdir / "source", opener)

    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)
    result = separator.separate(
        source, out_dir, on_progress or (lambda stage, pct: None)
    )

    # Дорожки уходят по ключам, которые задала наша сторона: повтор задачи
    # перезапишет то же самое, а не наплодит копий (дизайн, решение 3).
    _upload(uploads["vocals"], result.vocals, opener)
    _upload(uploads["no_vocals"], result.no_vocals, opener)

    return {
        "track_id": payload["track_id"],
        "degraded": bool(getattr(result, "degraded", False)),
        "elapsed_sec": round(time.perf_counter() - started, 2),
    }


def handler(job: dict) -> dict:
    """То, что вызывает RunPod. Обёртка над `separate_job`."""
    from ..separation.demucs_local import DemucsSeparator

    payload = job.get("input") or {}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            return separate_job(payload, DemucsSeparator(), Path(tmp))
        except BadJob as exc:
            # Кривую задачу повторять незачем — отвечаем ошибкой, а не падаем:
            # падение заставит RunPod повторять её до исчерпания попыток.
            log.warning("отклонена задача: %s", exc)
            return {"error": str(exc)}


def main() -> None:  # pragma: no cover — требует их SDK и GPU
    import runpod

    # Прогрев до объявления готовности: иначе первый запрос платит ≈20 с
    # JIT-компиляции ядер, и платит его человек ожиданием (4.5).
    from ..separation.demucs_local import DemucsSeparator

    DemucsSeparator().warmup()
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":  # pragma: no cover
    main()
