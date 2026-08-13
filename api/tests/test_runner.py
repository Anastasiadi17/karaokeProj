import dataclasses
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from karaoke_api import deps
from karaoke_api.config import Settings
from karaoke_api.jobs.models import JobStatus
from karaoke_api.jobs.runner import JobRunner
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.main import create_app
from karaoke_api.separation.fake import FakeSeparator
from karaoke_api.storage.local import LocalStorage


class ExplodingSeparator:
    def warmup(self):
        """Греть нечего: подделка не держит модели."""

    def separate(self, source, out_dir, on_progress):
        raise RuntimeError("CUDA out of memory")


class RecordingSeparator:
    """Записывает порядок вызовов.

    Прогрев проверяется порядком, а не таймингом: подделка греется за
    микросекунды, и любое измерение времени тут покажет шум.
    """

    def __init__(self):
        self.calls: list[str] = []

    def warmup(self):
        self.calls.append("warmup")

    def separate(self, source, out_dir, on_progress):
        self.calls.append("separate")
        return FakeSeparator().separate(source, out_dir, on_progress)


class FailingWarmupSeparator:
    """Прогрев падает, разделение работает.

    Ровно тот случай, ради которого прогрев не имеет права валить сервис:
    веса не скачались один раз, а ленивая загрузка потом справилась.
    """

    def warmup(self):
        raise RuntimeError("веса не скачались")

    def separate(self, source, out_dir, on_progress):
        return FakeSeparator().separate(source, out_dir, on_progress)


@pytest.fixture
def wiring(tmp_path, make_wav):
    store = JobStore(tmp_path / "db.sqlite")
    storage = LocalStorage(tmp_path / "store")
    work = tmp_path / "work"
    work.mkdir()

    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    storage.store_file(key, make_wav(duration_sec=1.0))
    store.create_track(track_id, "song.wav", key, 1.0)

    return store, storage, work, track_id


def test_run_once_returns_false_when_idle(wiring):
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is False


def test_successful_job_reaches_done(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.DONE
    assert job.stage is None
    assert set(job.result["stems"]) == {"vocals", "no_vocals"}
    # FakeSeparator деградировать не умеет, но поле обязано присутствовать
    # всегда — подсистеме B удобнее читать булево, чем проверять ключ.
    assert job.result["degraded"] is False


class _DegradedSeparator:
    """Подделка, воспроизводящая SeparationResult с degraded=True — так,
    как его вернул бы DemucsSeparator после реального отката на CPU."""

    def separate(self, source, out_dir, on_progress):
        result = FakeSeparator().separate(source, out_dir, on_progress)
        return dataclasses.replace(result, degraded=True)


def test_degraded_result_reaches_job_result(wiring):
    """Пометка о деградации обязана доехать от SeparationResult до
    результата задачи в базе — иначе подсистема B не сможет показать
    пользователю, что трек обработан медленным путём."""
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    runner = JobRunner(store, storage, _DegradedSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.DONE
    assert job.result["degraded"] is True


def test_stems_are_written_to_storage(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    JobRunner(store, storage, FakeSeparator(), work).run_once()

    stems = store.get_job(job_id).result["stems"]
    assert storage.exists(stems["vocals"])
    assert storage.exists(stems["no_vocals"])
    assert storage.size(stems["no_vocals"]) > 0


def test_failing_separator_marks_job_failed(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    runner = JobRunner(store, storage, ExplodingSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert "CUDA out of memory" in job.error_message


def test_work_dir_is_cleaned_after_job(wiring):
    store, storage, work, track_id = wiring
    store.create_job(track_id)

    JobRunner(store, storage, FakeSeparator(), work).run_once()

    assert list(work.iterdir()) == []


def test_stems_are_not_written_for_track_deleted_mid_job(wiring):
    """DELETE прилетел, пока задача считалась.

    Записать стемы после этого — значит заново создать каталог трека,
    которого нет в базе: его не увидит ни list_expired_tracks, ни DELETE.
    Файлы остались бы на диске навсегда и навсегда скачиваемыми.
    """
    store, storage, work, track_id = wiring
    store.create_job(track_id)

    class DeletingSeparator:
        def separate(self, source, out_dir, on_progress):
            storage.delete_prefix(f"tracks/{track_id}")
            store.delete_track(track_id)
            return FakeSeparator().separate(source, out_dir, on_progress)

    runner = JobRunner(store, storage, DeletingSeparator(), work)
    assert runner.run_once() is True

    assert not storage.exists(f"tracks/{track_id}/stems/vocals.wav")
    assert not storage.exists(f"tracks/{track_id}/stems/no_vocals.wav")
    assert storage.list_prefixes("tracks") == []


def test_failure_to_record_failure_does_not_escape_run_once(wiring, monkeypatch):
    """store.fail() внутри except сам не защищён — а именно он падает, когда
    базу закрыли под работающим потоком. Исключение из него уходит в
    брошенный future и теряется молча."""
    store, storage, work, track_id = wiring
    store.create_job(track_id)

    def exploding_fail(job_id, message):
        raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    monkeypatch.setattr(store, "fail", exploding_fail)

    runner = JobRunner(store, storage, ExplodingSeparator(), work)
    assert runner.run_once() is True


def test_runner_reports_idle_only_after_job_finishes(wiring):
    """wait_until_idle обязан быть ложью, пока задача считается, и правдой
    после — иначе выключение не на что опереться."""
    store, storage, work, track_id = wiring
    store.create_job(track_id)

    seen_during_job = []

    class WatchingSeparator:
        def separate(self, source, out_dir, on_progress):
            seen_during_job.append(runner.wait_until_idle(0))
            return FakeSeparator().separate(source, out_dir, on_progress)

    runner = JobRunner(store, storage, WatchingSeparator(), work)
    assert runner.run_once() is True

    assert seen_during_job == [False]
    assert runner.wait_until_idle(0) is True


def test_scratch_dir_creation_failure_marks_job_failed(wiring):
    """Сбой между claim_next() и открытием try не должен ронять run_once."""
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    # job_id детерминирован store.create_job, поэтому путь под scratch-
    # директорию задачи известен заранее — занимаем его файлом, чтобы
    # scratch.mkdir() внутри run_once упал.
    (work / job_id).write_text("занято")

    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED


def test_warmup_status_is_pending_before_the_loop_starts(wiring):
    """До запуска цикла честный ответ — «ещё не начинали», а не «готово»."""
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)

    assert runner.warmup_status.state == "pending"
    assert runner.warmup_status.detail is None
    assert runner.warmup_status.elapsed_sec is None


def test_warmup_reports_ready_with_elapsed_time(wiring):
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)

    runner._warmup()

    status = runner.warmup_status
    assert status.state == "ready"
    assert status.detail is None
    assert status.elapsed_sec is not None
    assert status.elapsed_sec >= 0


def test_failed_warmup_records_reason_and_does_not_stop_the_loop(wiring):
    """Отказ прогрева не должен лишать сервис всего остального.

    Загрузка треков, выдача готовых стемов и уборка от модели не зависят, а
    задача попробует загрузить модель сама и упадёт с честной причиной. Это
    та же линия, что у check_gpu, который старт не валит ни при каких
    обстоятельствах.
    """
    store, storage, work, track_id = wiring
    runner = JobRunner(store, storage, FailingWarmupSeparator(), work)
    job_id = store.create_job(track_id)

    runner._warmup()

    status = runner.warmup_status
    assert status.state == "failed"
    assert "RuntimeError" in status.detail
    assert "веса не скачались" in status.detail
    assert status.elapsed_sec is not None

    assert runner.run_once() is True
    assert store.get_job(job_id).status is JobStatus.DONE


def test_warmup_runs_before_the_first_job_is_claimed(tmp_path, monkeypatch,
                                                     make_wav):
    """Прогрев обязан случиться до того, как раннер возьмёт первую задачу.

    Иначе первый пользователь по-прежнему платит загрузку модели, и весь
    смысл прогрева теряется. Проверяется сквозь настоящий lifespan, потому
    что порядок задают именно он и run_forever, а не прямой вызов метода.
    """
    separator = RecordingSeparator()
    monkeypatch.setattr(deps, "build_separator", lambda s, gpu=None: separator)
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )

    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()

        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            body = client.get(f"/api/jobs/{ids['job_id']}").json()
            if body["status"] in ("done", "failed"):
                break
            time.sleep(0.05)

        assert body is not None and body["status"] == "done", (
            f"задача не досчитала за 10 с: {body}"
        )
        assert client.app.state.karaoke.runner.warmup_status.state == "ready"

    assert separator.calls[0] == "warmup", (
        f"первым вызовом был не прогрев: {separator.calls}"
    )
    assert "separate" in separator.calls
