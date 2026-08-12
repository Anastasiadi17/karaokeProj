import logging
from datetime import datetime, timedelta, timezone

from .jobs.store import JobStore
from .storage.base import Storage

log = logging.getLogger(__name__)


def purge_expired(store: JobStore, storage: Storage, ttl_hours: int,
                  now: datetime | None = None) -> int:
    """Удалить треки старше TTL вместе с файлами. Возвращает число удалённых.

    Один неудалимый трек (например, файл занят фоновым процессом на
    Windows и delete_prefix бросает PermissionError) не должен обрывать
    всю уборку: ошибка на одном треке логируется, и уборка продолжается
    со следующим.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    removed = 0
    for track_id in store.list_expired_tracks(cutoff):
        try:
            storage.delete_prefix(f"tracks/{track_id}")
            store.delete_track(track_id)
        except Exception:
            log.exception("не удалось удалить истёкший трек %s", track_id)
            continue
        removed += 1

    if removed:
        log.info("автоочистка удалила треков: %d", removed)
    return removed
