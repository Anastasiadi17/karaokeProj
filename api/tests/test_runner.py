import sqlite3

import pytest

from karaoke_api.jobs.models import JobStatus
from karaoke_api.jobs.runner import JobRunner
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.separation.fake import FakeSeparator
from karaoke_api.storage.local import LocalStorage


class ExplodingSeparator:
    def separate(self, source, out_dir, on_progress):
        raise RuntimeError("CUDA out of memory")


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
