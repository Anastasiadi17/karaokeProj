from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Stage(str, Enum):
    """Осмысленна только при status = RUNNING, иначе None."""

    LOADING = "loading"
    SEPARATING = "separating"
    WRITING = "writing"


@dataclass(frozen=True)
class Track:
    id: str
    filename: str
    storage_key: str
    duration_sec: float
    created_at: datetime


@dataclass(frozen=True)
class Job:
    id: str
    track_id: str
    status: JobStatus
    stage: Stage | None
    progress: float
    error_message: str | None
    result: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
