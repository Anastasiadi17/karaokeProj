"""Хранилище аккаунтов поверх той же SQLite, что треки и задачи.

Отдельная база не заводится сознательно (дизайн подсистемы C, решение 1):
горизонт MVP однопроцессный, и вторая база дала бы вторую точку отказа.
Соединение своё, а не общее с `JobStore`: у них разные жизненные циклы, и
переплетать их замки ради экономии одного файлового дескриптора незачем.

Пароли здесь не хранятся никогда, а токены не хранятся в открытом виде:
в базу уходит только SHA-256. Утечка файла базы не даёт войти ни под кем.
"""

import hashlib
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    track_id   TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operations_user
    ON operations(user_id, created_at);
"""

DEFAULT_LOGIN_TTL = timedelta(minutes=15)
DEFAULT_SESSION_TTL = timedelta(days=30)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    created_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_email(email: str) -> str:
    """Адрес приводится к одному виду до всего остального.

    Иначе `Ivan@Example.COM` и `ivan@example.com` заведут два аккаунта с
    отдельными лимитами, и второй человек заведёт, сам того не заметив.
    Регистр локальной части по RFC значим, но ни один почтовый провайдер
    этим не пользуется, а вред от расщепления аккаунта вполне реален.
    """
    return email.strip().lower()


def _hash_token(raw: str) -> str:
    """Соли нет намеренно: токен случайный на 256 бит, и словаря, от которого
    соль защищает, для него не существует."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AccountStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # К файлу теперь ходят два соединения одного процесса (это и JobStore).
        # Без ожидания редкое совпадение записей отдаёт «database is locked»
        # вместо того, чтобы подождать миллисекунду.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def connection(self):
        """Прямой доступ к соединению — для тестов и миграций."""
        with self._lock:
            yield self._conn

    # --- пользователи --------------------------------------------------

    def upsert_user(self, email: str) -> User:
        normalized = normalize_email(email)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (normalized,)
            ).fetchone()
            if row is None:
                user_id = uuid.uuid4().hex
                created = _now().isoformat()
                self._conn.execute(
                    "INSERT INTO users (id, email, created_at)"
                    " VALUES (?, ?, ?)",
                    (user_id, normalized, created),
                )
                self._conn.commit()
                return User(id=user_id, email=normalized,
                            created_at=_parse_dt(created))
        return User(id=row["id"], email=row["email"],
                    created_at=_parse_dt(row["created_at"]))

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return User(id=row["id"], email=row["email"],
                    created_at=_parse_dt(row["created_at"]))

    # --- одноразовые ссылки --------------------------------------------

    def create_login_token(self, email: str,
                           ttl: timedelta = DEFAULT_LOGIN_TTL) -> str:
        """Возвращает сырой токен для ссылки; в базу уходит только хеш.

        Пользователь здесь НЕ создаётся: иначе запрос ссылки на чужой адрес
        заводил бы аккаунты за чужой счёт, а база наполнялась бы мусором от
        первого же сканера.
        """
        raw = secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO login_tokens (token_hash, email, created_at,"
                " expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(raw), normalize_email(email), now.isoformat(),
                 (now + ttl).isoformat()),
            )
            self._conn.commit()
        return raw

    def consume_login_token(self, raw: str) -> User | None:
        """Гасит токен и отдаёт пользователя, заводя его при первом входе."""
        token_hash = _hash_token(raw)
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM login_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or row["used_at"] is not None:
                return None
            if _parse_dt(row["expires_at"]) <= now:
                return None

            self._conn.execute(
                "UPDATE login_tokens SET used_at = ? WHERE token_hash = ?",
                (now.isoformat(), token_hash),
            )
            self._conn.commit()
            email = row["email"]

        return self.upsert_user(email)

    # --- сессии ---------------------------------------------------------

    def create_session(self, user_id: str,
                       ttl: timedelta = DEFAULT_SESSION_TTL) -> str:
        raw = secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at,"
                " expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(raw), user_id, now.isoformat(),
                 (now + ttl).isoformat()),
            )
            self._conn.commit()
        return raw

    def user_for_session(self, raw: str) -> User | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
                (_hash_token(raw),),
            ).fetchone()
        if row is None or _parse_dt(row["expires_at"]) <= _now():
            return None
        return self.get_user(row["user_id"])

    def delete_session(self, raw: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (_hash_token(raw),)
            )
            self._conn.commit()

    # --- операции --------------------------------------------------------

    def record_operation(self, user_id: str, kind: str,
                         track_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO operations (id, user_id, kind, track_id,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, user_id, kind, track_id,
                 _now().isoformat()),
            )
            self._conn.commit()

    def count_operations(self, user_id: str, since: datetime) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM operations"
                " WHERE user_id = ? AND created_at >= ?",
                (user_id, since.isoformat()),
            ).fetchone()
        return int(row["n"])
