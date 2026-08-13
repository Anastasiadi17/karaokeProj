import threading

import pytest

from karaoke_api.track_lock import TrackLock, TrackLockBusy


def test_hold_is_exclusive():
    """Второй желающий не входит, пока первый внутри."""
    lock = TrackLock()
    inside = threading.Event()
    release = threading.Event()
    entered_second = threading.Event()

    def first():
        with lock.hold():
            inside.set()
            release.wait(10)

    def second():
        with lock.hold():
            entered_second.set()

    holder = threading.Thread(target=first)
    holder.start()
    assert inside.wait(10), "первый поток не вошёл в секцию"

    waiter = threading.Thread(target=second)
    waiter.start()
    assert not entered_second.wait(0.3), (
        "второй поток вошёл в секцию под занятым замком"
    )

    release.set()
    holder.join(10)
    assert entered_second.wait(10), "второй поток не вошёл после освобождения"
    waiter.join(10)


def test_hold_with_timeout_raises_when_busy():
    """Путь DELETE: ждать вечно нельзя, ответа ждёт живой клиент."""
    lock = TrackLock()
    inside = threading.Event()
    release = threading.Event()

    def holder_body():
        with lock.hold():
            inside.set()
            release.wait(10)

    holder = threading.Thread(target=holder_body)
    holder.start()
    assert inside.wait(10)

    try:
        with pytest.raises(TrackLockBusy):
            with lock.hold(timeout=0.05):
                pass
    finally:
        release.set()
        holder.join(10)


def test_lock_is_released_after_exception_inside_the_block():
    """Иначе один сбой в критической секции навсегда вешает и удаление, и
    воркера."""
    lock = TrackLock()

    with pytest.raises(ValueError):
        with lock.hold():
            raise ValueError("сбой внутри секции")

    with lock.hold(timeout=0.05):
        pass
