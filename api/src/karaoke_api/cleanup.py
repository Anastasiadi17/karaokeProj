import logging
from datetime import datetime, timedelta, timezone

from .jobs.store import JobStore
from .storage.base import Storage
from .track_lock import TrackLock

log = logging.getLogger(__name__)


def purge_track(store: JobStore, storage: Storage, track_lock: TrackLock,
                track_id: str, timeout: float | None = None) -> None:
    """Удалить файлы трека и его строку под замком.

    Под замком — потому что порознь эти два действия переплетаются с
    воркером, дописывающим стемы: он проверит строку до удаления, а запишет
    файлы после, и каталог переживёт строку. Подробности — в спеке
    2026-08-13-delete-race-design.md.

    Порядок обратный созданию: сначала файлы, потом строка. Если удаление
    файлов сорвалось, строка остаётся намеренно — без неё файлы стали бы
    сиротами, которых не найдёт ни уборка по TTL, ни повторный DELETE.
    """
    with track_lock.hold(timeout):
        storage.delete_prefix(f"tracks/{track_id}")
        store.delete_track(track_id)


def purge_expired(store: JobStore, storage: Storage, track_lock: TrackLock,
                  ttl_hours: int, now: datetime | None = None) -> int:
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
            # Без таймаута намеренно: уборка фоновая, спешить ей некуда.
            purge_track(store, storage, track_lock, track_id)
        except Exception:
            log.exception("не удалось удалить истёкший трек %s", track_id)
            continue
        removed += 1

    if removed:
        log.info("автоочистка удалила треков: %d", removed)
    return removed


def purge_orphan_track_dirs(store: JobStore, storage: Storage) -> int:
    """Удалить файлы треков, которых нет в базе. Возвращает число удалённых.

    Такой каталог не увидит уже никто: list_expired_tracks ходит по строкам
    таблицы tracks, DELETE — тоже. Появляется он, когда трек удалили, пока
    задача считалась: воркер дописывал стемы после DELETE и заново создавал
    каталог. Прямую гонку закрывает проверка в JobRunner, а эта сверка на
    старте — сеть под ней и под файлами, осиротевшими при падении процесса.

    Ошибка на одном каталоге не обрывает сверку: на Windows занятый файл —
    штатный сценарий.
    """
    removed = 0
    for track_id in storage.list_prefixes("tracks"):
        if store.get_track(track_id) is not None:
            continue
        try:
            storage.delete_prefix(f"tracks/{track_id}")
        except Exception:
            log.exception("не удалось удалить осиротевшие файлы трека %s",
                          track_id)
            continue
        removed += 1

    if removed:
        log.warning("удалено осиротевших каталогов треков: %d", removed)
    return removed
