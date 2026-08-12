import pytest
from fastapi.testclient import TestClient

from karaoke_api import deps
from karaoke_api.config import Settings
from karaoke_api.jobs.models import JobStatus
from karaoke_api.jobs.store import JobStore
from karaoke_api.main import create_app


@pytest.fixture
def slow_app(tmp_path, monkeypatch, slow_separator):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    monkeypatch.setattr(
        deps, "build_separator", lambda s, gpu=None: slow_separator
    )
    return settings, slow_separator


def test_shutdown_waits_for_running_job(slow_app, make_wav):
    """База не должна закрываться под работающим рабочим потоком.

    До починки: task.cancel() снимал только ожидание to_thread, store.close()
    бил по живому потоку sqlite3.ProgrammingError, задача навсегда
    оставалась в running.
    """
    settings, separator = slow_app

    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()
        assert separator.started.wait(10), "воркер не успел взять задачу"

    # Выход из контекста — это выключение приложения. Открываем базу заново:
    # состояние задачи должно быть досчитанным, а не брошенным.
    store = JobStore(settings.db_path)
    try:
        job = store.get_job(ids["job_id"])
    finally:
        store.close()

    assert job.status is JobStatus.DONE, (
        f"задача осталась в {job.status.value}: соединение с базой закрыли "
        "под работающим потоком воркера"
    )
