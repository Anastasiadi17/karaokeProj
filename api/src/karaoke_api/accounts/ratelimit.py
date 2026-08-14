"""Ограничение частоты запросов ссылки входа.

Без него сервис — открытая рассылка: кто угодно шлёт письма на любой чужой
адрес сколько угодно раз, и с нашего домена. Пока почта уходит в лог, это
безобидно; в день включения SMTP это становится оружием против чужих ящиков
и способом сжечь нашу репутацию отправителя.

Счётчик в памяти процесса, а не в базе — по тому же горизонту, что и
`TrackLock`: пока процесс один, память честнее и дешевле. Появится второй —
переносить придётся вместе с ним, и это записано в README.
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Скользящее окно: не больше `limit` событий на ключ за `window_sec`."""

    def __init__(self, limit: int, window_sec: float) -> None:
        self._limit = limit
        self._window = window_sec
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """True — можно; False — превышено. Разрешённая попытка засчитывается."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            # Старое выбрасывается здесь же: отдельная уборка на таком объёме
            # не нужна, а забытые ключи держали бы память вечно.
            while hits and moment - hits[0] > self._window:
                hits.popleft()
            if not hits:
                self._hits.pop(key, None)
                hits = self._hits[key]
            if len(hits) >= self._limit:
                return False
            hits.append(moment)
        return True
