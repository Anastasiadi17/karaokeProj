import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, JobStatus, Stage, Track

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    storage_key   TEXT NOT NULL,
    duration_sec  REAL NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    track_id      TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    stage         TEXT,
    progress      REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    result_json   TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def new_id() -> str:
    """Идентификатор трека или задачи."""
    return uuid.uuid4().hex


class JobStore:
    """Треки и задачи в SQLite. Одно соединение, сериализованный доступ."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # С появлением аккаунтов к файлу ходят два соединения одного процесса
        # (это и AccountStore). Замок сериализует только своё соединение, а
        # чужую запись без ожидания SQLite встретит «database is locked».
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)
        self._add_column_if_missing("tracks", "user_id", "TEXT")
        self._conn.commit()

    def _add_column_if_missing(self, table: str, column: str,
                               decl: str) -> None:
        """SQLite не знает `ADD COLUMN IF NOT EXISTS`, а миграций у нас нет:
        схема накатывается на каждом старте, и повторный ALTER упал бы."""
        existing = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {decl}"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- треки ---------------------------------------------------------

    def create_track(self, track_id: str, filename: str, storage_key: str,
                     duration_sec: float, user_id: str | None = None) -> str:
        """Идентификатор приходит снаружи: ключ в хранилище строится из него,
        поэтому к моменту вставки он уже известен."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO tracks (id, filename, storage_key, duration_sec,"
                " created_at, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                (track_id, filename, storage_key, duration_sec, _now(),
                 user_id),
            )
            self._conn.commit()
        return track_id

    def get_track(self, track_id: str) -> Track | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        if row is None:
            return None
        return Track(
            id=row["id"],
            filename=row["filename"],
            storage_key=row["storage_key"],
            duration_sec=row["duration_sec"],
            created_at=_parse_dt(row["created_at"]),
            user_id=row["user_id"],
        )

    def list_tracks_of(self, user_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tracks WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [r["id"] for r in rows]

    def list_expired_tracks(self, cutoff: datetime) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at FROM tracks"
            ).fetchall()
        return [r["id"] for r in rows if _parse_dt(r["created_at"]) < cutoff]

    def delete_track(self, track_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE track_id = ?", (track_id,))
            self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._conn.commit()

    # --- задачи --------------------------------------------------------

    def create_job(self, track_id: str) -> str:
        job_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, track_id, status, progress, created_at)"
                " VALUES (?, ?, ?, 0, ?)",
                (job_id, track_id, JobStatus.QUEUED.value, _now()),
            )
            self._conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._row_to_job(row) if row else None

    def claim_next(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
                (JobStatus.RUNNING.value, _now(), row["id"]),
            )
            self._conn.commit()
            return self.get_job(row["id"])

    def set_stage(self, job_id: str, stage: Stage, progress: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
                (stage.value, float(progress), job_id),
            )
            self._conn.commit()

    def finish(self, job_id: str, result: dict) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, stage = NULL, progress = 1.0,"
                " result_json = ?, finished_at = ? WHERE id = ?",
                (JobStatus.DONE.value, json.dumps(result), _now(), job_id),
            )
            self._conn.commit()

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, stage = NULL, error_message = ?,"
                " finished_at = ? WHERE id = ?",
                (JobStatus.FAILED.value, message, _now(), job_id),
            )
            self._conn.commit()

    def fail_orphans(self) -> int:
        """Задачи, пережившие смерть процесса, честно помечаются упавшими."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status = ?, stage = NULL, error_message = ?,"
                " finished_at = ? WHERE status = ?",
                (
                    JobStatus.FAILED.value,
                    "процесс был прерван во время обработки",
                    _now(),
                    JobStatus.RUNNING.value,
                ),
            )
            self._conn.commit()
            return cursor.rowcount

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            track_id=row["track_id"],
            status=JobStatus(row["status"]),
            stage=Stage(row["stage"]) if row["stage"] else None,
            progress=row["progress"],
            error_message=row["error_message"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            created_at=_parse_dt(row["created_at"]),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
        )
