from datetime import datetime, timedelta, timezone

import pytest

from karaoke_api.jobs.models import JobStatus, Stage
from karaoke_api.jobs.store import JobStore, new_id


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "test.db")


def _new_track(store, key=None, duration=12.5):
    track_id = new_id()
    return store.create_track(
        track_id, "song.wav", key or f"tracks/{track_id}/original.wav", duration
    )


def test_new_id_is_unique(store):
    assert new_id() != new_id()


def test_create_track_returns_given_id(store):
    track_id = new_id()
    assert store.create_track(track_id, "s.wav", "k", 1.0) == track_id
    assert store.get_track(track_id).storage_key == "k"


def test_created_job_is_queued(store):
    track_id = _new_track(store)
    job_id = store.create_job(track_id)
    job = store.get_job(job_id)
    assert job.status is JobStatus.QUEUED
    assert job.stage is None
    assert job.progress == 0.0
    assert job.track_id == track_id


def test_claim_next_moves_to_running(store):
    job_id = store.create_job(_new_track(store))
    claimed = store.claim_next()
    assert claimed.id == job_id
    assert store.get_job(job_id).status is JobStatus.RUNNING


def test_claim_next_returns_none_when_empty(store):
    assert store.claim_next() is None


def test_claim_next_is_fifo(store):
    first = store.create_job(_new_track(store))
    second = store.create_job(_new_track(store))
    assert store.claim_next().id == first
    assert store.claim_next().id == second


def test_set_stage_records_progress(store):
    job_id = store.create_job(_new_track(store))
    store.claim_next()
    store.set_stage(job_id, Stage.SEPARATING, 0.42)
    job = store.get_job(job_id)
    assert job.stage is Stage.SEPARATING
    assert job.progress == pytest.approx(0.42)


def test_finish_stores_result_and_clears_stage(store):
    job_id = store.create_job(_new_track(store))
    store.claim_next()
    store.finish(job_id, {"stems": {"vocals": "k1", "no_vocals": "k2"}})
    job = store.get_job(job_id)
    assert job.status is JobStatus.DONE
    assert job.stage is None
    assert job.progress == 1.0
    assert job.result == {"stems": {"vocals": "k1", "no_vocals": "k2"}}
    assert job.finished_at is not None


def test_fail_stores_message(store):
    job_id = store.create_job(_new_track(store))
    store.claim_next()
    store.fail(job_id, "CUDA out of memory")
    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_message == "CUDA out of memory"
    assert job.stage is None


def test_fail_orphans_marks_running_jobs_failed(store, tmp_path):
    job_id = store.create_job(_new_track(store))
    store.claim_next()

    reopened = JobStore(tmp_path / "test.db")
    count = reopened.fail_orphans()

    assert count == 1
    job = reopened.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert "прерван" in job.error_message


def test_fail_orphans_leaves_queued_alone(store, tmp_path):
    job_id = store.create_job(_new_track(store))
    assert JobStore(tmp_path / "test.db").fail_orphans() == 0
    assert store.get_job(job_id).status is JobStatus.QUEUED


def test_list_expired_tracks_respects_cutoff(store):
    track_id = _new_track(store)
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert store.list_expired_tracks(future) == [track_id]
    assert store.list_expired_tracks(past) == []


def test_delete_track_removes_track_and_jobs(store):
    track_id = _new_track(store)
    job_id = store.create_job(track_id)
    store.delete_track(track_id)
    assert store.get_track(track_id) is None
    assert store.get_job(job_id) is None
