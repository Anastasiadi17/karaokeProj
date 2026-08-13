# Прогрев модели при старте — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Модель demucs прогревается один раз при старте, в фоне, а `/api/health` честно показывает её состояние.

**Architecture:** `JobRunner.run_forever` перед входом в цикл опроса один раз прогревает разделитель в том же пуле потоков, где считаются задачи. Модели касается ровно один поток за всю жизнь процесса — гонки за её загрузку нет по построению. Событийный цикл при этом свободен, HTTP принимается с первой секунды.

**Tech Stack:** Python 3.12, FastAPI, pytest (без pytest-asyncio — в наборе нет ни одного async-теста, и вводить его здесь не нужно), torch + demucs под маркером `slow`.

**Спека:** `docs/superpowers/specs/2026-08-13-model-warmup-design.md`

## Global Constraints

- Рабочий каталог для всех команд — `api/`. Интерпретатор — `.venv/Scripts/python`.
- Быстрый набор (`pytest`) обязан работать **без torch и demucs**. Всё, что их импортирует, живёт в `tests/test_demucs_slow.py` под `pytest.importorskip(..., exc_type=ImportError)` и маркером `slow`.
- `filterwarnings = ["error", ...]` в `pyproject.toml`: любое новое предупреждение валит тест. Новых зависимостей задача не вводит.
- `/api/health` отвечает **всегда 200**. Поле `model` добавляется, существующие `gpu` и `separator` не меняются.
- `state` состояния прогрева — ровно четыре значения: `pending`, `loading`, `ready`, `failed`.
- `detail` заполняется только при `failed`. `elapsed_sec` заполняется при `ready` и `failed`, при `pending` и `loading` равен `None`.
- Прогрев **не имеет права** уронить старт приложения — та же линия, что у `check_gpu`.
- Комментарии и docstring — на русском, как во всём модуле.

---

## Порядок задач и почему он такой

`DemucsSeparator.warmup` появляется **первой**, до того как раннер начнёт его звать. Обратный порядок оставил бы промежуточное состояние, в котором запуск с `KARAOKE_SEPARATOR=demucs` падает с `AttributeError` на старте.

| Задача | Файлы | Тесты |
|---|---|---|
| 1. Прогрев `DemucsSeparator`: замок и пробный инференс | `separation/demucs_local.py` | `tests/test_demucs_slow.py` (`slow`) |
| 2. Протокол `warmup` и статус прогрева в раннере | `separation/base.py`, `separation/fake.py`, `jobs/runner.py`, `tests/conftest.py` | `tests/test_runner.py` |
| 3. Поле `model` в `/api/health` | `main.py` | `tests/test_gpu_check.py` |
| 4. Синхронизация документов | план A, спека A, `api/README.md` | — |

---

### Task 1: Прогрев `DemucsSeparator` — замок и пробный инференс

**Files:**
- Modify: `api/src/karaoke_api/separation/demucs_local.py`
- Test: `api/tests/test_demucs_slow.py`

**Interfaces:**
- Consumes: `DemucsSeparator._ensure_model()`, `DemucsSeparator._apply_with_fallback(model, wav) -> tuple[Tensor, bool]` — уже существуют.
- Produces: `DemucsSeparator.warmup() -> None`; константа модуля `WARMUP_SECONDS: float = 5.0`. На них опирается задача 2.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `api/tests/test_demucs_slow.py`:

```python
@pytest.mark.slow
def test_warmup_loads_model_once_and_separate_reuses_it(make_wav, tmp_path,
                                                        capsys):
    """Прогрев обязан оставить модель в памяти, а не загрузить и выбросить.

    Проверяется тождеством объекта, а не таймингом: если separate() строит
    модель заново, прогрев не снимает с первого пользователя ничего, а
    замер времени на прогретой машине этого не покажет — разница утонет в
    разбросе. Повторный warmup() проверяет идемпотентность, которую требует
    контракт протокола.
    """
    separator = DemucsSeparator()

    started = time.perf_counter()
    separator.warmup()
    elapsed = time.perf_counter() - started

    warmed = separator._model
    assert warmed is not None, "прогрев не оставил модель в памяти"

    separator.warmup()
    assert separator._model is warmed, "повторный прогрев перезагрузил модель"

    source = make_wav(duration_sec=1.0, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()
    separator.separate(source, out, lambda stage, pct: None)

    assert separator._model is warmed, "separate() загрузил модель заново"

    with capsys.disabled():
        print(
            f"\n=== ПРОГРЕВ ===\n"
            f"устройство: {separator.device}\n"
            f"занял:      {elapsed:.1f} с\n"
            f"==============="
        )
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python -m pytest tests/test_demucs_slow.py::test_warmup_loads_model_once_and_separate_reuses_it -m slow -s -v`
Expected: FAIL — `AttributeError: 'DemucsSeparator' object has no attribute 'warmup'`

- [ ] **Step 3: Добавить импорт и константу**

В `api/src/karaoke_api/separation/demucs_local.py` в блок импортов добавить `threading` (после `logging`, порядок алфавитный):

```python
import logging
import threading
from pathlib import Path
```

После `log = logging.getLogger(__name__)` добавить константу:

```python
# Длительность пробного тензора для warmup(). По замеру двумя точками
# (karaoke-context.md 4.5) счёт стоит ≈0,03 с на секунду звука, то есть пять
# секунд — это ≈0,15 с. При этом пять секунд попадают в обычную сегментную
# ветку apply_model, а не в вырожденную, то есть прогревают те же ядра, что
# и настоящая задача.
WARMUP_SECONDS = 5.0
```

- [ ] **Step 4: Завести замок в конструкторе**

Заменить тело `__init__`:

```python
    def __init__(self, model_name: str = "htdemucs",
                 device: str | None = None) -> None:
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model_name = model_name
        self._model = None
        self._model_lock = threading.Lock()
```

- [ ] **Step 5: Взять загрузку модели под замок**

Заменить `_ensure_model` целиком:

```python
    def _ensure_model(self):
        """Загрузить модель один раз. Повторные вызовы отдают ту же.

        Замок здесь страхует будущее, а не чинит настоящее. Инвариант такой:
        модели касается только рабочий поток раннера — прогрев идёт в нём же,
        первым делом в run_forever. Инвариант нигде больше не записан, а без
        замка любой второй вызывающий (фоновый поток, второй раннер) получил
        бы две параллельные загрузки: лишние секунды, лишняя видеопамять и
        ни одного сообщения о том, что что-то не так.

        Схема double-checked: быстрая проверка без замка на горячем пути,
        повторная — под ним, потому что между первой проверкой и захватом
        замка модель мог загрузить кто-то другой.
        """
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                log.info("загружаю модель %s на %s", self._model_name,
                         self.device)
                self._model = get_model(self._model_name).to(
                    self.device).eval()
        return self._model
```

- [ ] **Step 6: Реализовать `warmup`**

Добавить метод сразу после `_ensure_model`:

```python
    def warmup(self) -> None:
        """Загрузить модель и прогнать через неё короткую тишину.

        Издержки первого запроса — две разные величины: загрузка весов из
        локального кеша (≈2,6 с, наступает на первом _ensure_model в
        процессе) и JIT-компиляция ядер CUDA (≈20 с, наступает на первом
        настоящем apply_model на машине или в контейнере). Прогрев, который
        только грузит веса, снял бы с первого пользователя меньшую из двух.

        Прогон идёт через _apply_with_fallback — тот же путь, которым идут
        настоящие задачи. Прогрев через другой путь прогрел бы не те ядра.

        На CPU пробного инференса нет: компилировать нечего, создание
        примитивов oneDNN стоит миллисекунды, а прогон пяти секунд через
        htdemucs на процессоре — десятки секунд. Тратить их, чтобы не
        сэкономить ничего, незачем.

        Пометка о деградации наружу не идёт: warmup не создаёт
        SeparationResult. Откат на CPU на пяти секундах тишины означает, что
        что-то серьёзно не так, поэтому он логируется предупреждением.
        """
        model = self._ensure_model()
        if self.device != "cuda":
            return
        frames = int(WARMUP_SECONDS * model.samplerate)
        _, degraded = self._apply_with_fallback(model, torch.zeros(2, frames))
        if degraded:
            log.warning(
                "прогрев прошёл только на CPU: видеопамяти не хватило даже "
                "на %.0f с тишины", WARMUP_SECONDS,
            )
```

- [ ] **Step 7: Убедиться, что тест проходит**

Run: `.venv/Scripts/python -m pytest tests/test_demucs_slow.py::test_warmup_loads_model_once_and_separate_reuses_it -m slow -s -v`
Expected: PASS, в выводе блок «ПРОГРЕВ» с временем

- [ ] **Step 8: Прогнать весь медленный набор**

Run: `.venv/Scripts/python -m pytest -m slow -s`
Expected: PASS — правка `_ensure_model` не должна задеть замер и тесты отката

- [ ] **Step 9: Убедиться, что быстрый набор не задет**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `1 deselected`, время примерно как было (никто ещё не зовёт `warmup`)

- [ ] **Step 10: Commit**

```bash
git add api/src/karaoke_api/separation/demucs_local.py api/tests/test_demucs_slow.py
git commit -m "feat(api): DemucsSeparator.warmup — веса плюс пробный инференс на CUDA"
```

---

### Task 2: Протокол `warmup` и статус прогрева в раннере

**Files:**
- Modify: `api/src/karaoke_api/separation/base.py`
- Modify: `api/src/karaoke_api/separation/fake.py`
- Modify: `api/src/karaoke_api/jobs/runner.py`
- Modify: `api/tests/conftest.py` (подделка `SlowSeparator`)
- Test: `api/tests/test_runner.py`

**Interfaces:**
- Consumes: `DemucsSeparator.warmup()` из задачи 1; `JobStore.create_job(track_id) -> str`, `JobStore.get_job(job_id)`, `JobRunner.run_once() -> bool` — уже существуют.
- Produces: `WarmupStatus(state: str, detail: str | None = None, elapsed_sec: float | None = None)` — frozen dataclass в `jobs/runner.py`; `JobRunner.warmup_status -> WarmupStatus` (property); `JobRunner._warmup() -> None`. На них опирается задача 3.

- [ ] **Step 1: Написать падающие тесты**

В `api/tests/test_runner.py` добавить к импортам:

```python
import time

from fastapi.testclient import TestClient

from karaoke_api import deps
from karaoke_api.config import Settings
from karaoke_api.main import create_app
```

Рядом с существующим `ExplodingSeparator` добавить `warmup` и две новые подделки:

```python
class ExplodingSeparator:
    def warmup(self):
        """Греть нечего: подделка не держит модели."""

    def separate(self, source, out_dir, on_progress):
        raise RuntimeError("CUDA out of memory")


class RecordingSeparator:
    """Записывает порядок вызовов.

    Прогрев проверяется порядком, а не таймингом: подделка греется за
    микросекунды, и любое измерение времени тут покажет шум.
    """

    def __init__(self):
        self.calls: list[str] = []

    def warmup(self):
        self.calls.append("warmup")

    def separate(self, source, out_dir, on_progress):
        self.calls.append("separate")
        return FakeSeparator().separate(source, out_dir, on_progress)


class FailingWarmupSeparator:
    """Прогрев падает, разделение работает.

    Ровно тот случай, ради которого прогрев не имеет права валить сервис:
    веса не скачались один раз, а ленивая загрузка потом справилась.
    """

    def warmup(self):
        raise RuntimeError("веса не скачались")

    def separate(self, source, out_dir, on_progress):
        return FakeSeparator().separate(source, out_dir, on_progress)
```

В конец файла добавить четыре теста:

```python
def test_warmup_status_is_pending_before_the_loop_starts(wiring):
    """До запуска цикла честный ответ — «ещё не начинали», а не «готово»."""
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)

    assert runner.warmup_status.state == "pending"
    assert runner.warmup_status.detail is None
    assert runner.warmup_status.elapsed_sec is None


def test_warmup_reports_ready_with_elapsed_time(wiring):
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)

    runner._warmup()

    status = runner.warmup_status
    assert status.state == "ready"
    assert status.detail is None
    assert status.elapsed_sec is not None
    assert status.elapsed_sec >= 0


def test_failed_warmup_records_reason_and_does_not_stop_the_loop(wiring):
    """Отказ прогрева не должен лишать сервис всего остального.

    Загрузка треков, выдача готовых стемов и уборка от модели не зависят, а
    задача попробует загрузить модель сама и упадёт с честной причиной. Это
    та же линия, что у check_gpu, который старт не валит ни при каких
    обстоятельствах.
    """
    store, storage, work, track_id = wiring
    runner = JobRunner(store, storage, FailingWarmupSeparator(), work)
    job_id = store.create_job(track_id)

    runner._warmup()

    status = runner.warmup_status
    assert status.state == "failed"
    assert "RuntimeError" in status.detail
    assert "веса не скачались" in status.detail
    assert status.elapsed_sec is not None

    assert runner.run_once() is True
    assert store.get_job(job_id).status is JobStatus.DONE


def test_warmup_runs_before_the_first_job_is_claimed(tmp_path, monkeypatch,
                                                     make_wav):
    """Прогрев обязан случиться до того, как раннер возьмёт первую задачу.

    Иначе первый пользователь по-прежнему платит загрузку модели, и весь
    смысл прогрева теряется. Проверяется сквозь настоящий lifespan, потому
    что порядок задают именно он и run_forever, а не прямой вызов метода.
    """
    separator = RecordingSeparator()
    monkeypatch.setattr(deps, "build_separator", lambda s, gpu=None: separator)
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )

    with TestClient(create_app(settings)) as client:
        with open(make_wav(duration_sec=0.5), "rb") as fh:
            ids = client.post(
                "/api/tracks", files={"file": ("s.wav", fh, "audio/wav")}
            ).json()

        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            body = client.get(f"/api/jobs/{ids['job_id']}").json()
            if body["status"] in ("done", "failed"):
                break
            time.sleep(0.05)

        assert body is not None and body["status"] == "done", (
            f"задача не досчитала за 10 с: {body}"
        )
        assert client.app.state.karaoke.runner.warmup_status.state == "ready"

    assert separator.calls[0] == "warmup", (
        f"первым вызовом был не прогрев: {separator.calls}"
    )
    assert "separate" in separator.calls
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: FAIL — `AttributeError: 'JobRunner' object has no attribute 'warmup_status'` в трёх новых тестах и `... has no attribute '_warmup'` в двух

- [ ] **Step 3: Добавить `warmup` в протокол**

В `api/src/karaoke_api/separation/base.py` внутрь `class StemSeparator(Protocol)` — в конец docstring добавить абзац и объявить метод перед `separate`:

```python
class StemSeparator(Protocol):
    """Отделение вокала от аккомпанемента.

    Реализация обязана положить ровно два файла в out_dir с именами
    vocals.wav и no_vocals.wav и вызвать on_progress со стадиями
    loading, separating, writing в этом порядке.

    degraded в возвращённом SeparationResult обязан быть True, только если
    реализация была вынуждена откатиться на более медленный или более слабый
    путь ради того, чтобы вообще выполнить задачу (например, нехватка
    видеопамяти на GPU и повтор на CPU). По умолчанию False; реализации,
    которые деградировать не умеют (например, FakeSeparator), значение не
    трогают.

    warmup обязан довести реализацию до состояния, в котором первая
    настоящая задача не платит разовых издержек. Метод идемпотентен и
    безопасен при повторном вызове; реализации, которым греть нечего, не
    делают ничего. Метод обязательный намеренно: новая реализация
    разделителя не должна иметь возможности молча его пропустить.
    """

    def warmup(self) -> None: ...

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult: ...
```

- [ ] **Step 4: Реализовать `warmup` у подделок**

В `api/src/karaoke_api/separation/fake.py` добавить метод перед `separate`:

```python
    def warmup(self) -> None:
        """Греть нечего: подделка готова всегда, и это честно."""
```

В `api/tests/conftest.py` добавить тот же метод в `SlowSeparator` перед `separate`:

```python
    def warmup(self) -> None:
        """Греть нечего: подделка просто спит в separate."""
```

- [ ] **Step 5: Добавить `WarmupStatus` в раннер**

В `api/src/karaoke_api/jobs/runner.py` дополнить импорты:

```python
import asyncio
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
```

После `log = logging.getLogger(__name__)` добавить:

```python
@dataclass(frozen=True)
class WarmupStatus:
    """Состояние разового прогрева разделителя.

    state — ровно четыре значения: pending (цикл ещё не стартовал), loading
    (идёт прогрев), ready (прогрет), failed (не удался). detail заполняется
    только при failed и несёт тип и текст исключения. elapsed_sec
    заполняется при ready и failed (сколько прошло до отказа), при pending и
    loading равен None.

    Класс неизменяемый намеренно: наружу публикуется объект целиком, и
    читатель из событийного цикла не может застать половинчатое состояние.
    """

    state: str
    detail: str | None = None
    elapsed_sec: float | None = None
```

- [ ] **Step 6: Завести поле и свойство**

В `JobRunner.__init__` после `self._stopped = False` добавить:

```python
        self._warmup_status = WarmupStatus("pending")
```

Сразу после `__init__` добавить свойство:

```python
    @property
    def warmup_status(self) -> WarmupStatus:
        """Читается из событийного цикла, пишется рабочим потоком.

        Замок не нужен: публикуется неизменяемый объект целиком, а
        присваивание ссылки в CPython атомарно.
        """
        return self._warmup_status
```

- [ ] **Step 7: Реализовать `_warmup`**

Добавить метод перед `run_forever`:

```python
    def _warmup(self) -> None:
        """Разовый прогрев разделителя перед циклом опроса.

        Исключение наружу не выпускается намеренно: сервис без модели всё
        равно принимает загрузки, отдаёт уже готовые стемы и убирает мусор, а
        задача упадёт на своей загрузке модели и получит честную причину в
        поле error. Та же линия, что у check_gpu, который по контракту не
        валит старт ни при каких обстоятельствах.
        """
        self._warmup_status = WarmupStatus("loading")
        started = time.perf_counter()
        try:
            self._separator.warmup()
        except Exception as exc:
            log.exception("прогрев разделителя не удался")
            self._warmup_status = WarmupStatus(
                "failed",
                f"{type(exc).__name__}: {exc}",
                time.perf_counter() - started,
            )
            return
        elapsed = time.perf_counter() - started
        self._warmup_status = WarmupStatus("ready", None, elapsed)
        log.info("разделитель прогрет за %.1f с", elapsed)
```

- [ ] **Step 8: Позвать прогрев из `run_forever`**

Заменить `run_forever` целиком:

```python
    async def run_forever(self, poll_interval: float = 0.5) -> None:
        """Цикл опроса. Обработка идёт в пуле потоков, чтобы не блокировать
        событийный цикл FastAPI на десятки секунд.

        Прогрев — первым делом и в том же пуле. Тогда модели касается ровно
        один поток за всю жизнь процесса, и гонки за её загрузку нет по
        построению, а не по факту установленного замка. Приём HTTP при этом
        не задерживается: событийный цикл свободен, а задача, поставленная
        во время прогрева, дождётся его в очереди — модель ей всё равно
        нужна.

        Выключение во время прогрева: task.cancel() снимает только ожидание
        to_thread, рабочий поток дозагружает модель. Базы прогрев не
        касается, поэтому терять нечего (в отличие от run_once, ради
        которого существует wait_until_idle), а ждать процесс будет не
        дольше самой загрузки.
        """
        if not self._stopped:
            await asyncio.to_thread(self._warmup)
        while not self._stopped:
            try:
                did_work = await asyncio.to_thread(self.run_once)
            except Exception:
                # Цикл обязан пережить всё: иначе один сбой навсегда
                # останавливает обработку, а сервис продолжает отвечать по HTTP.
                log.exception("непредвиденный сбой в цикле обработки")
                did_work = False
            if not did_work:
                await asyncio.sleep(poll_interval)
```

- [ ] **Step 9: Убедиться, что тесты проходят**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: PASS, все четыре новых теста зелёные

- [ ] **Step 10: Прогнать весь быстрый набор**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `1 deselected`, вывод чистый (`filterwarnings = error` — никаких предупреждений)

- [ ] **Step 11: Commit**

```bash
git add api/src/karaoke_api/separation/base.py api/src/karaoke_api/separation/fake.py api/src/karaoke_api/jobs/runner.py api/tests/conftest.py api/tests/test_runner.py
git commit -m "feat(api): раннер прогревает разделитель до первой задачи"
```

---

### Task 3: Поле `model` в `/api/health`

**Files:**
- Modify: `api/src/karaoke_api/main.py` (обработчик `health`)
- Test: `api/tests/test_gpu_check.py`

**Interfaces:**
- Consumes: `JobRunner.warmup_status -> WarmupStatus` из задачи 2; `app.state.karaoke.runner`.
- Produces: ответ `GET /api/health` с ключом `model` — `{"state": str, "detail": str | None, "elapsed_sec": float | None}`.

- [ ] **Step 1: Написать падающий тест**

В `api/tests/test_gpu_check.py` добавить `import time` к импортам и новый тест сразу после `test_health_endpoint_exposes_gpu`:

```python
def test_health_reports_model_warmup_state(tmp_path):
    """Эндпоинт называется проверкой готовности — он обязан говорить и о
    модели, а не только о GPU.

    Опрос в цикле, а не одно обращение: прогрев идёт в фоне, и на момент
    выхода из lifespan-стартапа фоновая задача могла ещё не получить
    управление. С FakeSeparator это микросекунды, но гонка настоящая.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            body = client.get("/api/health").json()
            if body["model"]["state"] == "ready":
                break
            time.sleep(0.05)

    assert body is not None
    assert body["model"]["state"] == "ready", f"прогрев не дошёл до ready: {body}"
    assert body["model"]["detail"] is None
    assert body["model"]["elapsed_sec"] is not None
    # Существующие ключи не должны пострадать от добавления нового.
    assert "gpu" in body
    assert body["separator"] == "fake"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/Scripts/python -m pytest tests/test_gpu_check.py::test_health_reports_model_warmup_state -v`
Expected: FAIL — `KeyError: 'model'`

- [ ] **Step 3: Добавить поле в обработчик**

В `api/src/karaoke_api/main.py` заменить обработчик `health` целиком:

```python
    @app.get("/api/health")
    async def health(request: Request):
        gpu = request.app.state.gpu
        # Состояние прогрева живёт у раннера, а не в app.state рядом с gpu:
        # gpu пишется один раз до старта и больше не меняется, а прогрев
        # пишет рабочий поток раннера уже после.
        warmup = request.app.state.karaoke.runner.warmup_status
        return {
            "gpu": {
                "available": gpu.available,
                "device_name": gpu.device_name,
                "reason": gpu.reason,
                "hint": gpu.hint,
            },
            "separator": settings.separator,
            "model": {
                "state": warmup.state,
                "detail": warmup.detail,
                "elapsed_sec": warmup.elapsed_sec,
            },
        }
```

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `.venv/Scripts/python -m pytest tests/test_gpu_check.py -v`
Expected: PASS, включая существующий `test_health_endpoint_exposes_gpu`

- [ ] **Step 5: Прогнать весь быстрый набор**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, `1 deselected`

- [ ] **Step 6: Проверить руками, что прогрев виден в живом приложении**

Run: `.venv/Scripts/python -m uvicorn karaoke_api.main:app` и в другом окне `curl http://127.0.0.1:8000/api/health`
Expected: `model.state` равен `loading` в первые секунды и `ready` дальше; `elapsed_sec` — настоящее время загрузки. На машине без GPU-группы будет `failed` с причиной, и это тоже верный результат.

- [ ] **Step 7: Commit**

```bash
git add api/src/karaoke_api/main.py api/tests/test_gpu_check.py
git commit -m "feat(api): /api/health показывает состояние прогрева модели"
```

---

### Task 4: Синхронизация документов

**Files:**
- Modify: `docs/superpowers/plans/2026-08-11-processing-core.md:7`
- Modify: `docs/superpowers/specs/2026-08-11-karaoke-mvp-architecture-design.md` (диаграмма в §4, абзац про проверку GPU в §6)
- Modify: `api/README.md`

**Interfaces:**
- Consumes: поведение, отгруженное задачами 1–3.
- Produces: —

- [ ] **Step 1: Поправить шапку плана подсистемы A**

В `docs/superpowers/plans/2026-08-11-processing-core.md` строка 7, заменить фрагмент:

```
Модель demucs загружается один раз при старте и остаётся в памяти.
```

на:

```
Модель demucs прогревается один раз при старте — в фоне, в рабочем потоке раннера, приём запросов при этом не блокируется — и остаётся в памяти.
```

- [ ] **Step 2: Поправить диаграмму в спеке**

В `docs/superpowers/specs/2026-08-11-karaoke-mvp-architecture-design.md`, §4, в блоке диаграммы потока данных есть строка:

```
   ├──────────────────────────────▶      │  (модель уже в памяти)
```

Заменить её на:

```
   ├──────────────────────────────▶      │  (модель прогрета при старте)
```

Выравнивание псевдографики не трогать: меняется только текст в скобках, длина строки в диаграмме ни на что не завязана.

- [ ] **Step 3: Дополнить §6 спеки**

В `docs/superpowers/specs/2026-08-11-karaoke-mvp-architecture-design.md` после абзаца «**Проверка GPU при старте, а не на первой задаче.**» (он заканчивается словами «через три недели.») вставить новый абзац:

```markdown
**Прогрев модели при старте, но не в ущерб старту.** Раннер прогревает
разделитель первым делом, до цикла опроса, и в том же рабочем потоке —
поэтому модели касается ровно один поток и гонки за её загрузку нет.
Приём HTTP не задерживается: прогрев идёт в пуле потоков, событийный цикл
свободен. `GET /api/health` отвечает всегда 200 и несёт поле `model` с
состоянием `pending | loading | ready | failed`; при `failed` там же
причина. Отказ прогрева старт не валит — сервис продолжает принимать
загрузки и отдавать готовые стемы, а задача упадёт с честной причиной.
Подробности: `2026-08-13-model-warmup-design.md`.
```

- [ ] **Step 4: Дополнить README**

В `api/README.md` заменить строку:

```
Проверка готовности: <http://127.0.0.1:8000/api/health>
```

на:

```
Проверка готовности: <http://127.0.0.1:8000/api/health>

Эндпоинт отвечает всегда 200. Поле `model.state` показывает прогрев модели:
`loading` в первые секунды после старта, дальше `ready`. `failed` означает,
что прогрев не удался (причина — в `model.detail`); сервис при этом
работает, но задачи, скорее всего, будут падать по той же причине.
```

- [ ] **Step 5: Проверить, что старое обещание больше нигде не осталось**

Run: `grep -rn "загружается один раз при старте\|модель уже в памяти" docs/ api/ --include=*.md --include=*.py`
Expected: совпадений нет

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-08-11-processing-core.md docs/superpowers/specs/2026-08-11-karaoke-mvp-architecture-design.md api/README.md
git commit -m "docs: обещание про модель при старте стало правдой"
```

---

## Проверка после всех задач

- [ ] `.venv/Scripts/python -m pytest -q` — быстрый набор зелёный, `1 deselected`
- [ ] `.venv/Scripts/python -m pytest -m slow -s` — медленный набор зелёный, в выводе блоки «ЗАМЕР» и «ПРОГРЕВ»
- [ ] `git log --oneline` — четыре коммита, по одному на задачу
