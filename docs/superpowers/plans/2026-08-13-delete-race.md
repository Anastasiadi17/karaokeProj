# Окно гонки при удалении трека — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Удаление трека и запись стемов перестают переплетаться, поэтому каталог трека не может пережить его строку в базе.

**Architecture:** Один процессный замок `TrackLock` вокруг двух критических секций — «проверить строку, записать стемы, `finish`» у воркера и «удалить файлы, удалить строку» у обоих удаляющих путей. Переплетения не существует: DELETE либо целиком раньше проверки, либо целиком позже записи.

**Tech Stack:** Python 3.12, `threading.Lock`, FastAPI, pytest.

**Спека:** `docs/superpowers/specs/2026-08-13-delete-race-design.md`

## Global Constraints

- Рабочий каталог для всех команд — `api/`. Интерпретатор — `.venv/Scripts/python`.
- Быстрый набор обязан работать без torch и demucs. Ничего из этой задачи их не требует.
- `filterwarnings = ["error", ...]` в `pyproject.toml`: любое новое предупреждение валит тест.
- Схема базы, коды ответов и контракт с подсистемой B **не меняются**. `503 delete_failed` уже существует и переиспользуется.
- **Порядок захвата замков:** `TrackLock` берётся до внутреннего замка `JobStore`, никогда наоборот.
- Комментарии и docstring — на русском.

---

## Порядок задач

Проводка идёт раньше критической секции воркера: после задачи 2 замок берёт только удаление, что ничего не ломает и ничего пока не защищает. После задачи 3 окно закрыто.

| Задача | Файлы | Тесты |
|---|---|---|
| 1. `TrackLock` | `track_lock.py` (создать) | `tests/test_track_lock.py` (создать) |
| 2. `purge_track` и проводка | `cleanup.py`, `config.py`, `deps.py`, `main.py`, `jobs/runner.py` | `tests/test_cleanup.py` |
| 3. Критическая секция воркера | `jobs/runner.py` | `tests/test_cleanup.py` |
| 4. Классификация сбоя в логе | `jobs/runner.py` | `tests/test_runner.py` |

---

### Task 1: `TrackLock`

**Files:**
- Create: `api/src/karaoke_api/track_lock.py`
- Test: `api/tests/test_track_lock.py` (создать)

**Interfaces:**
- Produces: `TrackLock` с методом-контекстменеджером `hold(timeout: float | None = None)`; исключение `TrackLockBusy(RuntimeError)`. На них опираются задачи 2 и 3.

- [ ] **Step 1: Написать падающие тесты**

Создать `api/tests/test_track_lock.py`:

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest tests/test_track_lock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'karaoke_api.track_lock'`

- [ ] **Step 3: Написать модуль**

Создать `api/src/karaoke_api/track_lock.py`:

```python
import threading
from contextlib import contextmanager
from typing import Iterator


class TrackLockBusy(RuntimeError):
    """Замок не удалось взять за отведённое время."""


class TrackLock:
    """Сериализует изменения пары «строка трека + его файлы».

    Существование трека описано в двух местах сразу: строкой в таблице
    tracks и каталогом tracks/{id} в хранилище. Операция, меняющая эту пару,
    обязана быть неделимой относительно других таких операций. Иначе воркер,
    дописывающий стемы, и DELETE, сносящий каталог, переплетаются так, что
    файлы переживают строку, — а такие файлы не видит никто: выдача стемов
    спрашивает базу, уборка по TTL ходит по строкам таблицы.

    Замок отдельный, а не поле JobStore или Storage, потому что охраняет
    именно пару — то, что не принадлежит ни базе, ни хранилищу.

    Порядок захвата: TrackLock берётся ДО внутреннего замка JobStore и
    никогда наоборот. JobStore о TrackLock не знает вовсе, поэтому обратного
    порядка взяться неоткуда и взаимной блокировки не возникает.

    Решение внутрипроцессное и опирается на то, что воркер один, а процесс
    единственный. При переезде очереди на несколько воркеров понадобится
    мягкое удаление в базе — см. спеку
    docs/superpowers/specs/2026-08-13-delete-race-design.md.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @contextmanager
    def hold(self, timeout: float | None = None) -> Iterator[None]:
        """Держать замок на время блока.

        Без timeout ждёт сколько потребуется — так делают воркер и фоновая
        уборка, которым сдаваться некуда: воркер, бросивший ожидание, либо
        пишет стемы без замка (возвращая ту самую гонку), либо теряет
        досчитанную задачу. С timeout бросает TrackLockBusy — это путь
        DELETE, где ответа ждёт живой клиент.
        """
        # acquire ждёт без ограничения при timeout=-1; None туда передать
        # нельзя, отсюда преобразование.
        acquired = self._lock.acquire(
            timeout=-1 if timeout is None else timeout
        )
        if not acquired:
            raise TrackLockBusy(
                f"замок изменения треков занят дольше {timeout} с"
            )
        try:
            yield
        finally:
            self._lock.release()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_track_lock.py -v`
Expected: PASS, три теста

- [ ] **Step 5: Commit**

```bash
git add api/src/karaoke_api/track_lock.py api/tests/test_track_lock.py
git commit -m "feat(api): TrackLock — замок вокруг пары «строка трека и его файлы»"
```

---

### Task 2: `purge_track` и проводка замка

**Files:**
- Modify: `api/src/karaoke_api/cleanup.py`
- Modify: `api/src/karaoke_api/config.py`
- Modify: `api/src/karaoke_api/deps.py`
- Modify: `api/src/karaoke_api/main.py`
- Modify: `api/src/karaoke_api/jobs/runner.py` (только конструктор)
- Test: `api/tests/test_cleanup.py`

**Interfaces:**
- Consumes: `TrackLock`, `TrackLockBusy` из задачи 1.
- Produces: `cleanup.purge_track(store, storage, track_lock, track_id, timeout=None) -> None`; `AppState.track_lock: TrackLock`; `JobRunner(..., track_lock: TrackLock | None = None)` и поле `JobRunner._track_lock`; `Settings.track_lock_timeout_sec: float = 30.0`. Новая сигнатура `purge_expired(store, storage, track_lock, ttl_hours, now=None)`. На них опирается задача 3.

- [ ] **Step 1: Написать падающие тесты**

В `api/tests/test_cleanup.py` дополнить импорты:

```python
from karaoke_api.deps import AppState
from karaoke_api.cleanup import purge_expired, purge_orphan_track_dirs, purge_track
from karaoke_api.track_lock import TrackLock
```

(строка с `from karaoke_api.cleanup import ...` уже есть — заменить её целиком на приведённую.)

Добавить в конец файла два теста:

```python
def test_runner_shares_the_track_lock_with_app_state(tmp_path):
    """Замок обязан быть общим у раннера и удаления.

    Если раннер заведёт свой, гонка вернётся, а все остальные тесты
    останутся зелёными — ровно тот способ сломаться молча, ради которого
    параметр сделан необязательным. Поэтому проводка закреплена отдельно.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    state = AppState.build(settings)
    try:
        assert state.runner._track_lock is state.track_lock
    finally:
        state.store.close()


def test_delete_answers_503_when_the_lock_is_busy(tmp_path, make_wav):
    """Пока кто-то держит замок, DELETE не висит вечно, а отвечает уже
    существующим кодом. Новых кодов в контракте не появляется."""
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        track_lock_timeout_sec=0.05,
    )
    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()
        state = client.app.state.karaoke
        with state.track_lock.hold():
            response = client.delete(f"/api/tracks/{ids['track_id']}")

    assert response.status_code == 503
    assert response.json() == {"error": "delete_failed"}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL — `ImportError: cannot import name 'purge_track'`

- [ ] **Step 3: Добавить настройку таймаута**

В `api/src/karaoke_api/config.py` после `shutdown_wait_sec` добавить:

```python
    # Сколько DELETE ждёт замок изменения треков, прежде чем ответить 503.
    # Замок держит воркер на время записи двух дорожек — около секунды на
    # стемах по 100 МиБ, так что 30 с это примерно тридцатикратный запас и
    # при этом заметно меньше обычного таймаута HTTP-клиента. Воркеру и
    # фоновой уборке таймаут не нужен: сдаваться им некуда.
    track_lock_timeout_sec: float = 30.0
```

- [ ] **Step 4: Добавить `purge_track` и перевести на неё `purge_expired`**

В `api/src/karaoke_api/cleanup.py` дополнить импорты:

```python
from .jobs.store import JobStore
from .storage.base import Storage
from .track_lock import TrackLock
```

Добавить функцию перед `purge_expired`:

```python
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
```

Заменить сигнатуру и тело цикла в `purge_expired`:

```python
def purge_expired(store: JobStore, storage: Storage, track_lock: TrackLock,
                  ttl_hours: int, now: datetime | None = None) -> int:
```

и внутри цикла заменить две строки

```python
            storage.delete_prefix(f"tracks/{track_id}")
            store.delete_track(track_id)
```

на одну:

```python
            purge_track(store, storage, track_lock, track_id)
```

Таймаут здесь не передаётся намеренно: уборка фоновая, спешить ей некуда.

- [ ] **Step 5: Провести замок через `AppState`**

В `api/src/karaoke_api/deps.py` добавить импорт `from .track_lock import TrackLock` и заменить класс:

```python
@dataclass
class AppState:
    settings: Settings
    store: JobStore
    storage: LocalStorage
    separator: StemSeparator
    runner: JobRunner
    track_lock: TrackLock

    @classmethod
    def build(cls, settings: Settings,
              gpu: GpuStatus | None = None) -> "AppState":
        store = JobStore(settings.db_path)
        storage = LocalStorage(Path(settings.data_dir) / "files")
        separator = build_separator(settings, gpu)
        track_lock = TrackLock()
        runner = JobRunner(
            store, storage, separator, Path(settings.data_dir) / "work",
            track_lock=track_lock,
        )
        return cls(settings, store, storage, separator, runner, track_lock)
```

- [ ] **Step 6: Принять замок в раннере**

В `api/src/karaoke_api/jobs/runner.py` добавить импорт `from ..track_lock import TrackLock` и заменить сигнатуру и начало `__init__`:

```python
    def __init__(self, store: JobStore, storage: Storage,
                 separator: StemSeparator, work_dir: Path,
                 track_lock: TrackLock | None = None) -> None:
        self._store = store
        self._storage = storage
        self._separator = separator
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        # Замок необязателен только ради тестов, конструирующих раннер
        # напрямую. Главный код обязан передавать общий с удалением: иначе у
        # каждого свой, гонка возвращается, и заметить это нечем. Проводку
        # закрепляет test_runner_shares_the_track_lock_with_app_state.
        self._track_lock = track_lock or TrackLock()
        self._stopped = False
```

Остальное тело `__init__` (`self._warmup_status`, `self._idle`) оставить как есть.

- [ ] **Step 7: Перевести DELETE и лайфспан на `purge_track`**

В `api/src/karaoke_api/main.py` заменить импорт уборки и добавить импорт исключения:

```python
from .cleanup import purge_expired, purge_orphan_track_dirs, purge_track
from .config import Settings, get_settings
from .deps import AppState
from .gpu import check_gpu
from .jobs.store import new_id
from .ranges import parse_range
from .track_lock import TrackLockBusy
```

В `lifespan` заменить оба вызова `purge_expired`. Первый:

```python
        purge_expired(state.store, state.storage, state.track_lock,
                      settings.file_ttl_hours)
```

Второй — внутри `_cleanup_loop`:

```python
                    await asyncio.to_thread(
                        purge_expired, state.store, state.storage,
                        state.track_lock, settings.file_ttl_hours,
                    )
```

Заменить обработчик `delete_track` целиком:

```python
    @app.delete("/api/tracks/{track_id}", status_code=204)
    async def delete_track(request: Request, track_id: str):
        state: AppState = request.app.state.karaoke
        if state.store.get_track(track_id) is None:
            return _error("not_found", status=404)
        try:
            # Целиком в отдельный поток: ожидание замка не должно вставать
            # поперёк событийного цикла.
            await asyncio.to_thread(
                purge_track, state.store, state.storage, state.track_lock,
                track_id, state.settings.track_lock_timeout_sec,
            )
        except TrackLockBusy:
            log.warning("удаление трека %s не дождалось замка за %s с",
                        track_id, state.settings.track_lock_timeout_sec)
            return _error("delete_failed", status=503)
        except Exception:
            # На Windows файл, занятый работающей задачей, не удаляется
            # (WinError 32) — штатный сценарий, а не сбой сервиса, и отвечать
            # на него голым 500 без кода нечестно. Строку трека purge_track в
            # этом случае оставляет намеренно: без неё файлы стали бы
            # сиротами, а так их подберёт уборка по TTL.
            log.exception("не удалось удалить файлы трека %s", track_id)
            return _error("delete_failed", status=503)
        return Response(status_code=204)
```

- [ ] **Step 8: Обновить три существующих вызова `purge_expired` в тестах**

В `api/tests/test_cleanup.py` заменить:

- строка ~27: `assert purge_expired(store, storage, ttl_hours=24) == 0` → `assert purge_expired(store, storage, TrackLock(), ttl_hours=24) == 0`
- строка ~35: `assert purge_expired(store, storage, ttl_hours=24, now=future) == 1` → `assert purge_expired(store, storage, TrackLock(), ttl_hours=24, now=future) == 1`
- строка ~189: `removed = purge_expired(store, storage, ttl_hours=24, now=future)` → `removed = purge_expired(store, storage, TrackLock(), ttl_hours=24, now=future)`

Отдельный `TrackLock()` на вызов здесь уместен: эти тесты проверяют логику уборки, а не проводку замка, и конкурента у них нет.

- [ ] **Step 9: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py -v`
Expected: PASS, включая оба новых теста

- [ ] **Step 10: Прогнать весь быстрый набор**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `2 deselected`, вывод чистый

- [ ] **Step 11: Commit**

```bash
git add api/src/karaoke_api/cleanup.py api/src/karaoke_api/config.py api/src/karaoke_api/deps.py api/src/karaoke_api/main.py api/src/karaoke_api/jobs/runner.py api/tests/test_cleanup.py
git commit -m "feat(api): удаление трека идёт под замком, оба пути через purge_track"
```

---

### Task 3: Критическая секция воркера

**Files:**
- Modify: `api/src/karaoke_api/jobs/runner.py` (`_run_claimed`)
- Test: `api/tests/test_cleanup.py`

**Interfaces:**
- Consumes: `JobRunner._track_lock` и `cleanup.purge_track` из задачи 2.
- Produces: —

- [ ] **Step 1: Написать падающий тест**

В `api/tests/test_cleanup.py` дополнить импорты:

```python
import threading

from karaoke_api.jobs.runner import JobRunner
from karaoke_api.separation.fake import FakeSeparator
```

Добавить в конец файла подделку и тест:

```python
class BlockingStorage:
    """Останавливает воркера ровно внутри записи стемов.

    SlowSeparator сюда не годится: он тормозит separate, а остаточное окно
    живёт ПОСЛЕ него — между проверкой строки трека и записью файлов.
    Добраться до окна можно только задержкой внутри store_file.
    """

    def __init__(self, inner):
        self._inner = inner
        self.writing_stems = threading.Event()
        self.may_continue = threading.Event()

    def store_file(self, key, src):
        if "/stems/" in key:
            self.writing_stems.set()
            self.may_continue.wait(10)
        self._inner.store_file(key, src)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_delete_waits_for_stem_write_and_leaves_no_orphan(tmp_path, make_wav):
    """Настоящая гонка: DELETE приходит, когда воркер уже прошёл проверку
    строки и пишет стемы.

    Без замка удаление проходит немедленно, воркер дописывает каталог уже
    после rmtree, и файлы переживают строку. Их не видит ни выдача стемов
    (спрашивает базу), ни уборка по TTL (ходит по строкам) — подбирает
    только сверка каталогов при следующем старте процесса.
    """
    store = JobStore(tmp_path / "db.sqlite")
    inner = LocalStorage(tmp_path / "store")
    storage = BlockingStorage(inner)
    work = tmp_path / "work"
    work.mkdir()
    lock = TrackLock()

    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    inner.store_file(key, make_wav(duration_sec=0.5))
    store.create_track(track_id, "s.wav", key, 0.5)
    store.create_job(track_id)

    runner = JobRunner(store, storage, FakeSeparator(), work, track_lock=lock)

    worker = threading.Thread(target=runner.run_once)
    worker.start()
    assert storage.writing_stems.wait(10), "воркер не дошёл до записи стемов"

    deleter = threading.Thread(
        target=purge_track, args=(store, inner, lock, track_id)
    )
    deleter.start()
    deleter.join(0.5)
    assert deleter.is_alive(), (
        "удаление прошло, пока воркер писал стемы: замок не держится"
    )

    storage.may_continue.set()
    worker.join(10)
    deleter.join(10)

    try:
        assert store.get_track(track_id) is None
        assert inner.list_prefixes("tracks") == [], (
            "каталог трека пережил удаление"
        )
    finally:
        store.close()
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py::test_delete_waits_for_stem_write_and_leaves_no_orphan -v`
Expected: FAIL — `AssertionError: удаление прошло, пока воркер писал стемы: замок не держится`

Это и есть доказательство, что окно настоящее: воркер замок ещё не берёт.

- [ ] **Step 3: Взять критическую секцию под замок**

В `api/src/karaoke_api/jobs/runner.py`, в `_run_claimed`, заменить блок от `if self._store.get_track(job.track_id) is None:` до вызова `self._store.finish(...)` включительно на:

```python
            # Критическая секция: проверка строки, запись стемов и finish
            # обязаны быть неделимы относительно удаления. Иначе DELETE
            # успевает целиком между проверкой и записью, и каталог трека
            # переживает строку — такие файлы не видит ни выдача стемов, ни
            # уборка по TTL. Подробности — в спеке
            # 2026-08-13-delete-race-design.md.
            #
            # finish внутри секции намеренно: тогда инвариант «стемы на
            # диске ⟺ задача done» держится целиком, а не почти.
            with self._track_lock.hold():
                if self._store.get_track(job.track_id) is None:
                    # Трек удалили, пока задача считалась (DELETE или
                    # автоочистка по TTL). Строки задачи тоже уже нет,
                    # помечать нечего.
                    log.info("трек %s удалён во время обработки, стемы не "
                             "пишем", job.track_id)
                    return True

                stems = {}
                for name, path in (("vocals", result.vocals),
                                   ("no_vocals", result.no_vocals)):
                    key = f"tracks/{job.track_id}/stems/{name}.wav"
                    self._storage.store_file(key, path)
                    stems[name] = key

                self._store.finish(
                    job.id, {"stems": stems, "degraded": result.degraded}
                )
```

`return True` изнутри `with` корректен: `finally` в объемлющем `try` по-прежнему убирает scratch, а замок освобождается контекстным менеджером.

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py -v`
Expected: PASS

- [ ] **Step 5: Прогнать весь быстрый набор**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `2 deselected`

- [ ] **Step 6: Commit**

```bash
git add api/src/karaoke_api/jobs/runner.py api/tests/test_cleanup.py
git commit -m "fix(api): каталог трека больше не может пережить его строку"
```

---

### Task 4: Классификация сбоя в логе

**Files:**
- Modify: `api/src/karaoke_api/jobs/runner.py` (`except`-ветка `_run_claimed`, новый `_track_is_gone`)
- Test: `api/tests/test_runner.py`

**Interfaces:**
- Consumes: `JobStore.get_track`.
- Produces: `JobRunner._track_is_gone(track_id: str) -> bool`.

- [ ] **Step 1: Написать падающий тест**

В `api/tests/test_runner.py` дополнить импорты:

```python
import logging
```

Добавить в конец файла подделку и тест:

```python
class DeletingOnMaterializeStorage:
    """Удаляет трек ровно тогда, когда воркер идёт за исходником.

    Воспроизводит DELETE, попавший между claim_next и materialize: задача
    уже взята в работу, а файла под ней больше нет. Замок этот случай не
    закрывает намеренно — копирование исходника в критическую секцию не
    входит, иначе DELETE ждал бы всю обработку.
    """

    def __init__(self, inner, store, track_id):
        self._inner = inner
        self._store = store
        self._track_id = track_id

    def materialize(self, key, dest_dir):
        self._inner.delete_prefix(f"tracks/{self._track_id}")
        self._store.delete_track(self._track_id)
        return self._inner.materialize(key, dest_dir)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_delete_during_processing_is_logged_without_traceback(wiring, caplog):
    """Удаление трека во время обработки — штатное действие пользователя.

    Полный traceback на него превращает лог в шум и топит в нём настоящие
    сбои, на которые действительно надо смотреть.
    """
    store, storage, work, track_id = wiring
    store.create_job(track_id)
    blocking = DeletingOnMaterializeStorage(storage, store, track_id)

    runner = JobRunner(store, blocking, FakeSeparator(), work)
    with caplog.at_level(logging.INFO, logger="karaoke_api.jobs.runner"):
        assert runner.run_once() is True

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], (
        f"штатное удаление залогировано как сбой: "
        f"{[r.getMessage() for r in errors]}"
    )
    assert any("удалён во время обработки" in r.getMessage()
               for r in caplog.records), (
        f"нет внятного сообщения о причине: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py::test_delete_during_processing_is_logged_without_traceback -v`
Expected: FAIL — `AssertionError: штатное удаление залогировано как сбой: ['задача ... упала']`

- [ ] **Step 3: Классифицировать сбой**

В `api/src/karaoke_api/jobs/runner.py` заменить первую строку `except`-ветки в `_run_claimed`:

```python
        except Exception as exc:
            if self._track_is_gone(job.track_id):
                # Трек удалили, пока задача считалась: исходник исчез
                # из-под materialize. Это штатное действие пользователя, а
                # не сбой сервиса — полный traceback здесь только зашумляет
                # лог и топит в нём настоящие сбои.
                log.info("трек %s удалён во время обработки, задача "
                         "прервана: %s", job.track_id, exc)
            else:
                log.exception("задача %s упала", job.id)
```

Остальное тело ветки (вложенный `try` вокруг `self._store.fail`) оставить как есть.

- [ ] **Step 4: Добавить `_track_is_gone`**

Добавить метод сразу после `_run_claimed`:

```python
    def _track_is_gone(self, track_id: str) -> bool:
        """Пропал ли трек из базы — для классификации сбоя задачи.

        При выключении соединение с базой может быть уже закрыто, и сам этот
        вопрос бросит исключение. Тогда честнее считать трек живым: лишний
        traceback дешевле проглоченного настоящего сбоя.
        """
        try:
            return self._store.get_track(track_id) is None
        except Exception:
            return False
```

- [ ] **Step 5: Убедиться, что тест проходит**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: PASS, включая существующие тесты падения задачи

- [ ] **Step 6: Прогнать весь быстрый набор**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `2 deselected`

- [ ] **Step 7: Commit**

```bash
git add api/src/karaoke_api/jobs/runner.py api/tests/test_runner.py
git commit -m "fix(api): удаление трека во время обработки больше не пишет traceback"
```

---

## Проверка после всех задач

- [ ] `.venv/Scripts/python -m pytest -q` — быстрый набор зелёный, `2 deselected`
- [ ] `.venv/Scripts/python -m pytest -m slow -s` — медленный набор зелёный (задача его не касается, но регрессию исключить надо)
- [ ] `git log --oneline` — четыре коммита, по одному на задачу
