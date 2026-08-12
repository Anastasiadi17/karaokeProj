# Ядро обработки (подсистема A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HTTP-сервис, который принимает аудиофайл, отделяет минусовку через demucs и отдаёт дорожки браузеру.

**Architecture:** FastAPI с фоновым исполнителем задач в том же процессе. Модель demucs загружается один раз при старте и остаётся в памяти. Три шва описаны протоколами: `StemSeparator`, `Storage`, очередь (`JobStore` + `JobRunner`). Весь конвейер тестируется без GPU через `FakeSeparator`.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic-settings, demucs 4.x, PyTorch (сборка cu128+), soundfile, SQLite (stdlib `sqlite3`), pytest, httpx.

## Global Constraints

- **Интерпретатор: Python 3.12.** В системе стоит 3.14.2, но колёса PyTorch для 3.14 могут отсутствовать. Задача 1 проверяет это явно и откатывается на 3.12.
- **PyTorch только со сборкой под CUDA 12.8 или новее.** GPU — RTX 5060, архитектура Blackwell, compute capability 12.0 (sm_120). Обычный `pip install torch` ставит несовместимую сборку.
- **Все пути к данным берутся из `Settings`,** ни один путь не пишется в коде напрямую.
- **Лимиты загрузки:** форматы mp3/wav/flac (m4a исключён — libsndfile его не читает), длительность до 600 с, размер до 104857600 байт, TTL файлов 24 ч.
- **Формат определяется разбором содержимого,** а не расширением файла.
- **Быстрые тесты не требуют GPU и не вызывают настоящий demucs.** Реальная модель — только в тестах с маркером `slow`.
- Рабочая директория для всех команд: `api/`.

---

### Task 1: Каркас проекта и конфигурация

**Files:**
- Create: `api/pyproject.toml`
- Create: `api/.env.example`
- Create: `api/src/karaoke_api/__init__.py`
- Create: `api/src/karaoke_api/config.py`
- Create: `api/tests/__init__.py`
- Create: `api/tests/test_config.py`

**Interfaces:**
- Consumes: ничего
- Produces: `Settings` (pydantic BaseSettings) с полями `data_dir: Path`, `db_path: Path`, `separator: str`, `max_duration_sec: int`, `max_upload_bytes: int`, `file_ttl_hours: int`, `allowed_formats: tuple[str, ...]`, `shutdown_wait_sec: float`; функция `get_settings() -> Settings`.

- [ ] **Step 1: Проверить доступность интерпретатора и колёс PyTorch**

```bash
py -0p
```

Ожидается список установленных интерпретаторов. Нужен 3.12. Если его нет — поставить:

```bash
winget install --id Python.Python.3.12 -e
```

Проверить, что колесо torch под cu128 существует для 3.12:

```bash
py -3.12 -m pip index versions torch --index-url https://download.pytorch.org/whl/cu128
```

Ожидается непустой список версий. Если команда падает — зафиксировать вывод и остановиться, это блокер для задачи 12.

- [ ] **Step 2: Создать виртуальное окружение**

```bash
cd api
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -U pip
```

- [ ] **Step 3: Написать `pyproject.toml`**

```toml
[project]
name = "karaoke-api"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic-settings>=2.6",
    "python-multipart>=0.0.12",
    "soundfile>=0.12",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/karaoke_api"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["slow: требует GPU и настоящую модель demucs"]
addopts = "-m 'not slow'"
filterwarnings = [
    "error",
    # Стороннее предупреждение из starlette.testclient, наш код его не вызывает.
    # Категория именно StarletteDeprecationWarning: она наследует UserWarning,
    # а не DeprecationWarning, поэтому обычная категория не совпала бы.
    "ignore:Using `httpx` with `starlette.testclient` is deprecated:starlette.exceptions.StarletteDeprecationWarning",
]
```

`"error"` первым пунктом превращает любое другое предупреждение в падение —
именно это удерживает вывод честным. Точечный ignore задан и по тексту, и по
категории, чтобы не глушить ничего постороннего.

- [ ] **Step 4: Установить зависимости**

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

- [ ] **Step 5: Написать падающий тест**

Создать `api/tests/test_config.py`:

```python
from pathlib import Path

from karaoke_api.config import Settings


def test_defaults_match_spec():
    s = Settings()
    assert s.max_duration_sec == 600
    assert s.max_upload_bytes == 104857600
    assert s.file_ttl_hours == 24
    assert s.allowed_formats == ("mp3", "wav", "flac")
    assert s.separator == "demucs"


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("KARAOKE_SEPARATOR", "fake")
    monkeypatch.setenv("KARAOKE_MAX_DURATION_SEC", "30")
    s = Settings()
    assert s.separator == "fake"
    assert s.max_duration_sec == 30


def test_data_paths_are_pathlib(tmp_path, monkeypatch):
    monkeypatch.setenv("KARAOKE_DATA_DIR", str(tmp_path))
    s = Settings()
    assert isinstance(s.data_dir, Path)
    assert s.data_dir == tmp_path
```

- [ ] **Step 6: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.config'`

- [ ] **Step 7: Написать реализацию**

Создать `api/src/karaoke_api/__init__.py` (пустой файл) и `api/src/karaoke_api/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KARAOKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    db_path: Path = Path("data/karaoke.db")

    separator: str = "demucs"

    max_duration_sec: int = 600
    max_upload_bytes: int = 104857600
    file_ttl_hours: int = 24
    allowed_formats: tuple[str, ...] = ("mp3", "wav", "flac")

    # Сколько ждать текущую задачу при выключении, прежде чем закрыть базу.
    # Замер на RTX 5060 на предельной длительности: трек в
    # max_duration_sec=600 обрабатывается за 20,9 с вместе с загрузкой
    # модели. 120 — примерно пятикратный запас на карту послабее и на
    # копирование стемов (по 100 МиБ каждый), и при этом приемлемое время
    # ответа на Ctrl+C. Это потолок ожидания, а не гарантия: на откате на
    # CPU обработка длится куда дольше, и тогда выключение честно логирует,
    # что задача не досчитана.
    shutdown_wait_sec: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Создать `api/tests/__init__.py` (пустой файл).

- [ ] **Step 8: Запустить тест, убедиться что проходит**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: PASS, 3 теста

- [ ] **Step 9: Написать `.env.example`**

```
KARAOKE_DATA_DIR=data
KARAOKE_DB_PATH=data/karaoke.db
KARAOKE_SEPARATOR=demucs
KARAOKE_MAX_DURATION_SEC=600
KARAOKE_MAX_UPLOAD_BYTES=104857600
KARAOKE_FILE_TTL_HOURS=24
```

- [ ] **Step 10: Коммит**

```bash
cd ..
git add api/pyproject.toml api/.env.example api/src api/tests
git commit -m "feat(api): каркас проекта и конфигурация"
```

---

### Task 2: Хранилище за протоколом

**Files:**
- Create: `api/src/karaoke_api/storage/__init__.py`
- Create: `api/src/karaoke_api/storage/base.py`
- Create: `api/src/karaoke_api/storage/local.py`
- Create: `api/tests/test_storage.py`

**Interfaces:**
- Consumes: `Settings` из Task 1
- Produces: протокол `Storage` с методами `store_file(key: str, src: Path) -> None`, `materialize(key: str, dest_dir: Path) -> Path`, `read_range(key: str, start: int, length: int) -> bytes`, `iter_range(key: str, start: int, length: int, chunk_size: int = 65536) -> Iterator[bytes]`, `size(key: str) -> int`, `exists(key: str) -> bool`, `list_prefixes(prefix: str) -> list[str]`, `delete_prefix(prefix: str) -> None`; класс `LocalStorage(root: Path)`.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_storage.py`:

```python
import pytest

from karaoke_api.storage.local import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "store")


@pytest.fixture
def source_file(tmp_path):
    p = tmp_path / "src.bin"
    p.write_bytes(b"0123456789")
    return p


def test_store_and_size(storage, source_file):
    storage.store_file("tracks/abc/original.bin", source_file)
    assert storage.exists("tracks/abc/original.bin")
    assert storage.size("tracks/abc/original.bin") == 10


def test_missing_key_is_not_exists(storage):
    assert not storage.exists("tracks/nope/x.bin")


def test_materialize_copies_to_dest(storage, source_file, tmp_path):
    storage.store_file("tracks/abc/original.bin", source_file)
    dest_dir = tmp_path / "work"
    dest_dir.mkdir()
    out = storage.materialize("tracks/abc/original.bin", dest_dir)
    assert out.parent == dest_dir
    assert out.read_bytes() == b"0123456789"


def test_read_range_returns_slice(storage, source_file):
    storage.store_file("k", source_file)
    assert storage.read_range("k", 2, 3) == b"234"


def test_read_range_clamps_past_end(storage, source_file):
    storage.store_file("k", source_file)
    assert storage.read_range("k", 8, 100) == b"89"


def test_iter_range_streams_in_chunks(storage, source_file):
    storage.store_file("k", source_file)
    chunks = list(storage.iter_range("k", 0, 10, chunk_size=4))
    assert chunks == [b"0123", b"4567", b"89"]


def test_iter_range_stops_at_end_of_file(storage, source_file):
    storage.store_file("k", source_file)
    assert b"".join(storage.iter_range("k", 8, 100)) == b"89"


def test_iter_range_respects_start(storage, source_file):
    storage.store_file("k", source_file)
    assert b"".join(storage.iter_range("k", 5, 3)) == b"567"


def test_delete_prefix_removes_subtree(storage, source_file):
    storage.store_file("tracks/abc/a.bin", source_file)
    storage.store_file("tracks/abc/b.bin", source_file)
    storage.store_file("tracks/xyz/c.bin", source_file)
    storage.delete_prefix("tracks/abc")
    assert not storage.exists("tracks/abc/a.bin")
    assert not storage.exists("tracks/abc/b.bin")
    assert storage.exists("tracks/xyz/c.bin")


def test_key_traversal_is_rejected(storage, source_file):
    with pytest.raises(ValueError):
        storage.store_file("../escape.bin", source_file)


def test_key_resolving_to_root_is_rejected(storage, source_file):
    with pytest.raises(ValueError):
        storage.store_file("tracks/..", source_file)


def test_key_with_interior_traversal_to_root_is_rejected(storage, source_file):
    with pytest.raises(ValueError):
        storage.store_file("a/b/../..", source_file)


def test_delete_prefix_nonexistent_is_noop(storage):
    storage.delete_prefix("tracks/никогда-не-существовал")
```

Три последних теста закрывают границу безопасности: ключ не должен ни выходить
за пределы хранилища, ни совпадать с его корнем.

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_storage.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.storage'`

- [ ] **Step 3: Написать протокол**

Создать `api/src/karaoke_api/storage/__init__.py` (пустой) и `api/src/karaoke_api/storage/base.py`:

```python
from pathlib import Path
from typing import Iterator, Protocol


class Storage(Protocol):
    """Хранилище файлов. Локальная реализация сейчас, объектное хранилище потом.

    Ключ — строка вида "tracks/<id>/original.mp3". Разделитель всегда "/",
    независимо от платформы.
    """

    def store_file(self, key: str, src: Path) -> None:
        """Положить файл с диска под ключом."""
        ...

    def materialize(self, key: str, dest_dir: Path) -> Path:
        """Выложить содержимое ключа реальным файлом в dest_dir.

        Нужно, потому что demucs работает с путями, а не с потоками.
        """
        ...

    def read_range(self, key: str, start: int, length: int) -> bytes:
        """Прочитать до length байт начиная со start. Обрезается по концу файла."""
        ...

    def iter_range(
        self, key: str, start: int, length: int, chunk_size: int = 65536
    ) -> Iterator[bytes]:
        """То же, но кусками. Дорожка весит десятки мегабайт, и целиком в
        память её тянуть незачем."""
        ...

    def size(self, key: str) -> int: ...

    def exists(self, key: str) -> bool: ...

    def list_prefixes(self, prefix: str) -> list[str]:
        """Непосредственные подпрефиксы под prefix.

        Для "tracks" — идентификаторы всех треков, у которых есть файлы.
        В объектном хранилище это ListObjects с delimiter="/".
        """
        ...

    def delete_prefix(self, prefix: str) -> None:
        """Удалить все ключи, начинающиеся с prefix."""
        ...
```

- [ ] **Step 4: Написать локальную реализацию**

Создать `api/src/karaoke_api/storage/local.py`:

```python
import contextlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Iterator


class LocalStorage:
    """Storage поверх файловой системы. Ключ отображается в путь под root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/"):
            raise ValueError(f"недопустимый ключ: {key!r}")
        path = (self._root / key).resolve()
        root = self._root.resolve()
        # Path.parents никогда не содержит сам путь, поэтому ключ, схлопнувшийся
        # ровно в корень (например "tracks/.."), тоже отвергается — иначе
        # delete_prefix снёс бы всё хранилище.
        if root not in path.parents:
            raise ValueError(f"ключ выходит за пределы хранилища: {key!r}")
        return path

    def store_file(self, key: str, src: Path) -> None:
        """Положить файл под ключом. Ключ появляется целиком или не появляется.

        Копирование прямо в целевой путь означало бы, что клиент, запросивший
        дорожку в момент записи, получит частично записанный WAV с кодом 200
        и Content-Length, снятым в гонке. Поэтому копия ложится рядом во
        временный файл, а os.replace переставляет её атомарно — в том числе
        на Windows.
        """
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Суффикс уникален: два одновременных store_file на один ключ не
        # должны дописывать друг другу один и тот же временный файл.
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    def materialize(self, key: str, dest_dir: Path) -> Path:
        src = self._resolve(key)
        dest = Path(dest_dir) / src.name
        shutil.copyfile(src, dest)
        return dest

    def read_range(self, key: str, start: int, length: int) -> bytes:
        with self._resolve(key).open("rb") as fh:
            fh.seek(start)
            return fh.read(length)

    def iter_range(
        self, key: str, start: int, length: int, chunk_size: int = 65536
    ) -> Iterator[bytes]:
        remaining = length
        with self._resolve(key).open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(chunk_size, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    def size(self, key: str) -> int:
        return self._resolve(key).stat().st_size

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except ValueError:
            return False

    def list_prefixes(self, prefix: str) -> list[str]:
        try:
            target = self._resolve(prefix)
        except ValueError:
            return []
        if not target.is_dir():
            return []
        return sorted(p.name for p in target.iterdir() if p.is_dir())

    def delete_prefix(self, prefix: str) -> None:
        # Ошибки удаления не глушим: молча провалившаяся уборка по TTL
        # неотличима от успешной, а диск при этом заполняется.
        target = self._resolve(prefix)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_storage.py -v`
Expected: PASS, 13 тестов

- [ ] **Step 6: Коммит**

```bash
cd .. && git add api/src/karaoke_api/storage api/tests/test_storage.py
git commit -m "feat(api): хранилище за протоколом Storage"
```

---

### Task 3: Валидация загружаемого аудио

**Files:**
- Create: `api/src/karaoke_api/audio/__init__.py`
- Create: `api/src/karaoke_api/audio/probe.py`
- Create: `api/tests/conftest.py`
- Create: `api/tests/test_probe.py`

**Interfaces:**
- Consumes: `Settings` из Task 1
- Produces: `AudioInfo` (dataclass: `duration_sec: float`, `sample_rate: int`, `channels: int`, `format: str`); `probe_audio(path: Path) -> AudioInfo`; `normalize_format(container: str) -> str` (сводит семейство wav — `wav`/`wavex`/`rf64`/`w64` — к одному имени для проверки по `allowed_formats`); исключение `UnsupportedAudio(Exception)`; фикстура `make_wav` в `conftest.py`.

- [ ] **Step 1: Написать общую фикстуру генерации аудио**

Создать `api/tests/conftest.py`:

```python
import math
import wave
from pathlib import Path

import pytest


def _write_wav(path: Path, duration_sec: float, sample_rate: int = 44100,
               channels: int = 2, freq: float = 440.0) -> Path:
    frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = bytearray()
        for i in range(frames):
            value = int(20000 * math.sin(2 * math.pi * freq * i / sample_rate))
            data += value.to_bytes(2, "little", signed=True) * channels
        wf.writeframes(bytes(data))
    return path


@pytest.fixture
def make_wav(tmp_path):
    """Создаёт настоящий WAV заданной длительности. Возвращает Path."""

    def _make(name: str = "t.wav", duration_sec: float = 1.0,
              sample_rate: int = 44100, channels: int = 2) -> Path:
        return _write_wav(tmp_path / name, duration_sec, sample_rate, channels)

    return _make
```

- [ ] **Step 2: Написать падающий тест**

Создать `api/tests/test_probe.py`:

```python
import pytest

from karaoke_api.audio.probe import AudioInfo, UnsupportedAudio, probe_audio


def test_reads_wav_metadata(make_wav):
    path = make_wav(duration_sec=2.0, sample_rate=44100, channels=2)
    info = probe_audio(path)
    assert isinstance(info, AudioInfo)
    assert info.sample_rate == 44100
    assert info.channels == 2
    assert info.format == "wav"
    assert abs(info.duration_sec - 2.0) < 0.05


def test_mono_is_reported_as_one_channel(make_wav):
    info = probe_audio(make_wav(channels=1))
    assert info.channels == 1


def test_non_audio_raises(tmp_path):
    junk = tmp_path / "notaudio.mp3"
    junk.write_bytes(b"this is definitely not audio")
    with pytest.raises(UnsupportedAudio):
        probe_audio(junk)


def test_extension_is_ignored_content_decides(make_wav, tmp_path):
    wav = make_wav(name="real.wav")
    renamed = tmp_path / "liar.mp3"
    renamed.write_bytes(wav.read_bytes())
    info = probe_audio(renamed)
    assert info.format == "wav"
```

- [ ] **Step 3: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_probe.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.audio'`

- [ ] **Step 4: Написать реализацию**

Создать `api/src/karaoke_api/audio/__init__.py` (пустой) и `api/src/karaoke_api/audio/probe.py`:

```python
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf


class UnsupportedAudio(Exception):
    """Файл не является читаемым аудио."""


# libsndfile возвращает имя КОНТЕЙНЕРА, а allowed_formats — список
# пользовательских форматов (по сути расширений). Для семейства wav это
# разные словари: WAVEX — это WAVE_FORMAT_EXTENSIBLE, обычный .wav, который
# штатно выдают многие редакторы и рекордеры; RF64 и W64 — те же данные RIFF
# с 64-битными размерами для файлов свыше 4 ГБ. Все три libsndfile читает без
# проблем, и обрабатываем мы их одинаково. Без сведения к одному имени сервис
# отвечал «формат не поддерживается» на файл, который прекрасно умеет
# обработать (замер на libsndfile 1.2.2: 'WAVEX' -> wavex -> 400).
_WAV_FAMILY = frozenset({"wav", "wavex", "rf64", "w64"})


def normalize_format(container: str) -> str:
    """Свести имя контейнера от libsndfile к имени формата из политики."""
    return "wav" if container in _WAV_FAMILY else container


@dataclass(frozen=True)
class AudioInfo:
    duration_sec: float
    sample_rate: int
    channels: int
    format: str


def probe_audio(path: Path) -> AudioInfo:
    """Разобрать аудиофайл по содержимому. Расширение игнорируется."""
    try:
        info = sf.info(str(path))
        # Деление обязано быть внутри try: путь недоверенного ввода, и
        # samplerate == 0 в заголовке дал бы ZeroDivisionError, то есть 500
        # вместо честного 400 unsupported_format.
        duration_sec = float(info.frames) / float(info.samplerate)
    except Exception as exc:
        raise UnsupportedAudio(str(exc)) from exc

    return AudioInfo(
        duration_sec=duration_sec,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
        format=str(info.format).lower(),
    )
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_probe.py -v`
Expected: PASS, 4 теста

Примечание: `soundfile` читает wav/flac/ogg нативно; mp3 поддерживается начиная с libsndfile 1.1, входящей в колесо `soundfile>=0.12`. Если тест на mp3 в задаче 7 упадёт — это сигнал, что колесо старое, поставить `soundfile` заново.

- [ ] **Step 6: Коммит**

```bash
cd .. && git add api/src/karaoke_api/audio api/tests/conftest.py api/tests/test_probe.py
git commit -m "feat(api): разбор и валидация аудио по содержимому"
```

---

### Task 4: Протокол разделения и фейковая реализация

**Files:**
- Create: `api/src/karaoke_api/separation/__init__.py`
- Create: `api/src/karaoke_api/separation/base.py`
- Create: `api/src/karaoke_api/separation/fake.py`
- Create: `api/tests/test_fake_separator.py`

**Interfaces:**
- Consumes: ничего
- Produces: `SeparationResult` (frozen dataclass: `vocals: Path`, `no_vocals: Path`); протокол `StemSeparator` с методом `separate(source: Path, out_dir: Path, on_progress: ProgressCallback) -> SeparationResult`; тип `ProgressCallback = Callable[[str, float], None]`; класс `FakeSeparator`.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_fake_separator.py`:

```python
from karaoke_api.separation.fake import FakeSeparator


def test_produces_two_stems(make_wav, tmp_path):
    source = make_wav(duration_sec=1.0)
    out = tmp_path / "out"
    out.mkdir()

    result = FakeSeparator().separate(source, out, lambda stage, pct: None)

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()
    assert result.vocals.name == "vocals.wav"
    assert result.no_vocals.name == "no_vocals.wav"


def test_reports_stages_in_order(make_wav, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    seen: list[str] = []

    FakeSeparator().separate(
        make_wav(), out, lambda stage, pct: seen.append(stage)
    )

    assert seen == ["loading", "separating", "writing"]


def test_stems_are_readable_audio(make_wav, tmp_path):
    from karaoke_api.audio.probe import probe_audio

    out = tmp_path / "out"
    out.mkdir()
    result = FakeSeparator().separate(make_wav(duration_sec=1.5), out,
                                      lambda s, p: None)

    info = probe_audio(result.no_vocals)
    assert abs(info.duration_sec - 1.5) < 0.05
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_fake_separator.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.separation'`

- [ ] **Step 3: Написать протокол**

Создать `api/src/karaoke_api/separation/__init__.py` (пустой) и `api/src/karaoke_api/separation/base.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

ProgressCallback = Callable[[str, float], None]
"""Вызывается как on_progress(stage, fraction). fraction в диапазоне 0..1."""


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    no_vocals: Path


class StemSeparator(Protocol):
    """Отделение вокала от аккомпанемента.

    Реализация обязана положить ровно два файла в out_dir с именами
    vocals.wav и no_vocals.wav и вызвать on_progress со стадиями
    loading, separating, writing в этом порядке.
    """

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult: ...
```

- [ ] **Step 4: Написать фейк**

Создать `api/src/karaoke_api/separation/fake.py`:

```python
import shutil
from pathlib import Path

from .base import ProgressCallback, SeparationResult


class FakeSeparator:
    """Подделка для тестов: копирует исходник в обе дорожки.

    Нужна, чтобы весь конвейер — очередь, стадии, выдача файлов — можно было
    проверить за миллисекунды без GPU и без настоящей модели.
    """

    def separate(
        self,
        source: Path,
        out_dir: Path,
        on_progress: ProgressCallback,
    ) -> SeparationResult:
        on_progress("loading", 0.0)
        on_progress("separating", 0.5)

        vocals = Path(out_dir) / "vocals.wav"
        no_vocals = Path(out_dir) / "no_vocals.wav"

        on_progress("writing", 0.9)
        shutil.copyfile(source, vocals)
        shutil.copyfile(source, no_vocals)

        return SeparationResult(vocals=vocals, no_vocals=no_vocals)
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_fake_separator.py -v`
Expected: PASS, 3 теста

- [ ] **Step 6: Коммит**

```bash
cd .. && git add api/src/karaoke_api/separation api/tests/test_fake_separator.py
git commit -m "feat(api): протокол StemSeparator и фейковая реализация"
```

---

### Task 5: Модель задач и хранилище задач в SQLite

**Files:**
- Create: `api/src/karaoke_api/jobs/__init__.py`
- Create: `api/src/karaoke_api/jobs/models.py`
- Create: `api/src/karaoke_api/jobs/store.py`
- Create: `api/tests/test_job_store.py`

**Interfaces:**
- Consumes: ничего
- Produces: перечисления `JobStatus` (`QUEUED`/`RUNNING`/`DONE`/`FAILED`) и `Stage` (`LOADING`/`SEPARATING`/`WRITING`); dataclass `Track` и `Job`; функция `new_id() -> str`; класс `JobStore(db_path: Path)` с методами `create_track(track_id, filename, storage_key, duration_sec) -> str`, `get_track`, `create_job`, `get_job`, `claim_next`, `set_stage`, `finish`, `fail`, `fail_orphans`, `list_expired_tracks`, `delete_track`.

Идентификатор трека приходит снаружи: ключ в хранилище строится из него, и
запись должна попадать в базу сразу с готовым ключом, а не дополняться вторым
запросом.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_job_store.py`:

```python
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
    # Стадию надо выставить, иначе она None с самого начала и проверка
    # «снята после finish» ничего не доказывает.
    store.set_stage(job_id, Stage.SEPARATING, 0.5)
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
    store.set_stage(job_id, Stage.SEPARATING, 0.5)
    store.fail(job_id, "CUDA out of memory")
    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error_message == "CUDA out of memory"
    assert job.stage is None


def test_fail_orphans_marks_running_jobs_failed(store, tmp_path):
    job_id = store.create_job(_new_track(store))
    store.claim_next()
    store.set_stage(job_id, Stage.LOADING, 0.1)

    reopened = JobStore(tmp_path / "test.db")
    count = reopened.fail_orphans()

    assert count == 1
    job = reopened.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.stage is None
    assert "прерван" in job.error_message


def test_fail_orphans_leaves_queued_alone(store, tmp_path):
    job_id = store.create_job(_new_track(store))
    assert JobStore(tmp_path / "test.db").fail_orphans() == 0
    assert store.get_job(job_id).status is JobStatus.QUEUED


def test_list_expired_tracks_respects_cutoff(store):
    track_id = _new_track(store)
    # cutoff в прошлом — трек ещё жив; cutoff в будущем — уже просрочен.
    # Брать cutoff = now нельзя: трек создан микросекундами раньше и всегда
    # окажется просроченным.
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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_job_store.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.jobs'`

- [ ] **Step 3: Написать модели**

Создать `api/src/karaoke_api/jobs/__init__.py` (пустой) и `api/src/karaoke_api/jobs/models.py`:

```python
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
```

- [ ] **Step 4: Написать хранилище**

Создать `api/src/karaoke_api/jobs/store.py`:

```python
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
    """Треки и задачи в SQLite. Одно соединение, сериализованный доступ.

    ВАЖНО: каждый публичный метод, обращающийся к self._conn, обязан обернуть
    весь свой доступ в `with self._lock:`. check_same_thread=False отключает не
    опасность, а предупреждение о ней: воркер пишет из рабочего потока, а
    эндпоинт статуса читает из потока событийного цикла, и без сериализации
    выборка строки затирается на лету (наблюдалось: status приходил None из
    колонки NOT NULL).

    Замок реентрантный, потому что claim_next вызывает get_job уже под ним.
    В claim_next SELECT и UPDATE обязаны выполняться под одним захватом.
    """

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- треки ---------------------------------------------------------

    def create_track(self, track_id: str, filename: str, storage_key: str,
                     duration_sec: float) -> str:
        """Идентификатор приходит снаружи: ключ в хранилище строится из него,
        поэтому к моменту вставки он уже известен."""
        self._conn.execute(
            "INSERT INTO tracks (id, filename, storage_key, duration_sec,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (track_id, filename, storage_key, duration_sec, _now()),
        )
        self._conn.commit()
        return track_id

    def get_track(self, track_id: str) -> Track | None:
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
        )

    def list_expired_tracks(self, cutoff: datetime) -> list[str]:
        rows = self._conn.execute(
            "SELECT id, created_at FROM tracks"
        ).fetchall()
        return [r["id"] for r in rows if _parse_dt(r["created_at"]) < cutoff]

    def delete_track(self, track_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE track_id = ?", (track_id,))
        self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        self._conn.commit()

    # --- задачи --------------------------------------------------------

    def create_job(self, track_id: str) -> str:
        job_id = new_id()
        self._conn.execute(
            "INSERT INTO jobs (id, track_id, status, progress, created_at)"
            " VALUES (?, ?, ?, 0, ?)",
            (job_id, track_id, JobStatus.QUEUED.value, _now()),
        )
        self._conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def claim_next(self) -> Job | None:
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
        self._conn.execute(
            "UPDATE jobs SET stage = ?, progress = ? WHERE id = ?",
            (stage.value, float(progress), job_id),
        )
        self._conn.commit()

    def finish(self, job_id: str, result: dict) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ?, stage = NULL, progress = 1.0,"
            " result_json = ?, finished_at = ? WHERE id = ?",
            (JobStatus.DONE.value, json.dumps(result), _now(), job_id),
        )
        self._conn.commit()

    def fail(self, job_id: str, message: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = ?, stage = NULL, error_message = ?,"
            " finished_at = ? WHERE id = ?",
            (JobStatus.FAILED.value, message, _now(), job_id),
        )
        self._conn.commit()

    def fail_orphans(self) -> int:
        """Задачи, пережившие смерть процесса, честно помечаются упавшими."""
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
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_job_store.py -v`
Expected: PASS, 13 тестов

- [ ] **Step 6: Коммит**

```bash
cd .. && git add api/src/karaoke_api/jobs api/tests/test_job_store.py
git commit -m "feat(api): модель задач и хранилище в SQLite"
```

---

### Task 6: Фоновый исполнитель задач

**Files:**
- Create: `api/src/karaoke_api/jobs/runner.py`
- Create: `api/tests/test_runner.py`

**Interfaces:**
- Consumes: `JobStore` (Task 5), `Storage` (Task 2), `StemSeparator`/`FakeSeparator` (Task 4)
- Produces: класс `JobRunner(store, storage, separator, work_dir)` с методами `run_once() -> bool` и `async run_forever(poll_interval: float)`, `stop()`.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_runner.py`:

```python
import pytest

from karaoke_api.jobs.models import JobStatus
from karaoke_api.jobs.runner import JobRunner
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.separation.fake import FakeSeparator
from karaoke_api.storage.local import LocalStorage


class ExplodingSeparator:
    def separate(self, source, out_dir, on_progress):
        raise RuntimeError("CUDA out of memory")


@pytest.fixture
def wiring(tmp_path, make_wav):
    store = JobStore(tmp_path / "db.sqlite")
    storage = LocalStorage(tmp_path / "store")
    work = tmp_path / "work"
    work.mkdir()

    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    storage.store_file(key, make_wav(duration_sec=1.0))
    store.create_track(track_id, "song.wav", key, 1.0)

    return store, storage, work, track_id


def test_run_once_returns_false_when_idle(wiring):
    store, storage, work, _ = wiring
    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is False


def test_successful_job_reaches_done(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.DONE
    assert job.stage is None
    assert set(job.result["stems"]) == {"vocals", "no_vocals"}


def test_stems_are_written_to_storage(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    JobRunner(store, storage, FakeSeparator(), work).run_once()

    stems = store.get_job(job_id).result["stems"]
    assert storage.exists(stems["vocals"])
    assert storage.exists(stems["no_vocals"])
    assert storage.size(stems["no_vocals"]) > 0


def test_failing_separator_marks_job_failed(wiring):
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    runner = JobRunner(store, storage, ExplodingSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert "CUDA out of memory" in job.error_message


def test_work_dir_is_cleaned_after_job(wiring):
    store, storage, work, track_id = wiring
    store.create_job(track_id)

    JobRunner(store, storage, FakeSeparator(), work).run_once()

    assert list(work.iterdir()) == []


def test_scratch_dir_creation_failure_marks_job_failed(wiring):
    """Сбой между claim_next() и открытием try не должен ронять run_once."""
    store, storage, work, track_id = wiring
    job_id = store.create_job(track_id)

    # job_id детерминирован store.create_job, поэтому путь под scratch-
    # директорию задачи известен заранее — занимаем его файлом, чтобы
    # scratch.mkdir() внутри run_once упал.
    (work / job_id).write_text("занято")

    runner = JobRunner(store, storage, FakeSeparator(), work)
    assert runner.run_once() is True

    job = store.get_job(job_id)
    assert job.status is JobStatus.FAILED
```

Последний тест закрывает главный отказ подсистемы: если исключение вылетит из
`run_once`, фоновый цикл умрёт молча, а сервис продолжит отвечать по HTTP.

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.jobs.runner'`

- [ ] **Step 3: Написать реализацию**

Создать `api/src/karaoke_api/jobs/runner.py`:

```python
import asyncio
import logging
import shutil
import threading
from pathlib import Path

from ..separation.base import StemSeparator
from ..storage.base import Storage
from .models import Stage
from .store import JobStore

log = logging.getLogger(__name__)


class JobRunner:
    """Забирает задачи из очереди и прогоняет их через разделитель.

    Работает в одном экземпляре: GPU один, параллелить нечего.
    """

    def __init__(self, store: JobStore, storage: Storage,
                 separator: StemSeparator, work_dir: Path) -> None:
        self._store = store
        self._storage = storage
        self._separator = separator
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._stopped = False
        # Взведено, пока рабочий поток ничего не считает. Нужно при
        # выключении: см. wait_until_idle.
        self._idle = threading.Event()
        self._idle.set()

    def run_once(self) -> bool:
        """Обработать одну задачу. False — очередь пуста."""
        self._idle.clear()
        try:
            return self._run_claimed()
        finally:
            self._idle.set()

    def wait_until_idle(self, timeout: float) -> bool:
        """Дождаться, пока текущая задача досчитает. False — не дождались.

        Обязательна перед закрытием базы при выключении. task.cancel() на
        run_forever снимает только ожидание asyncio.to_thread — сам рабочий
        поток продолжает считать. Если закрыть под ним соединение, он упадёт
        на первом же set_stage/finish, а исключение уйдёт в брошенный future
        и потеряется молча, оставив задачу навсегда в running.
        """
        return self._idle.wait(timeout)

    def _run_claimed(self) -> bool:
        job = self._store.claim_next()
        if job is None:
            return False

        scratch = self._work_dir / job.id
        try:
            # mkdir обязан быть внутри try: сбой между claim_next и except
            # оставил бы задачу навсегда в RUNNING и вынес исключение в цикл.
            scratch.mkdir(parents=True, exist_ok=True)
            track = self._store.get_track(job.track_id)
            source = self._storage.materialize(track.storage_key, scratch)

            def on_progress(stage: str, fraction: float) -> None:
                self._store.set_stage(job.id, Stage(stage), fraction)

            result = self._separator.separate(source, scratch, on_progress)

            if self._store.get_track(job.track_id) is None:
                # Трек удалили, пока задача считалась (DELETE или автоочистка
                # по TTL). Записать стемы сейчас — значит заново создать
                # каталог трека, которого нет в базе: его не увидит ни
                # list_expired_tracks, ни DELETE, и файлы останутся навсегда.
                # Строки задачи тоже уже нет, помечать нечего.
                log.info("трек %s удалён во время обработки, стемы не пишем",
                         job.track_id)
                return True

            stems = {}
            for name, path in (("vocals", result.vocals),
                               ("no_vocals", result.no_vocals)):
                key = f"tracks/{job.track_id}/stems/{name}.wav"
                self._storage.store_file(key, path)
                stems[name] = key

            self._store.finish(job.id, {"stems": stems})
        except Exception as exc:
            log.exception("задача %s упала", job.id)
            try:
                self._store.fail(job.id, f"{type(exc).__name__}: {exc}")
            except Exception:
                # Запись отказа сама может не пройти (закрытое соединение,
                # удалённая строка задачи). Тогда исходное исключение уже
                # залогировано, и терять run_once из-за этого нельзя:
                # наружу оно уходит в брошенный future и пропадает молча.
                log.exception("не удалось пометить задачу %s упавшей", job.id)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        return True

    def stop(self) -> None:
        self._stopped = True

    async def run_forever(self, poll_interval: float = 0.5) -> None:
        """Цикл опроса. Обработка идёт в пуле потоков, чтобы не блокировать
        событийный цикл FastAPI на десятки секунд."""
        while not self._stopped:
            try:
                did_work = await asyncio.to_thread(self.run_once)
            except Exception:
                # Цикл обязан пережить всё: иначе один сбой навсегда
                # останавливает обработку, а сервис продолжает отвечать по HTTP.
                # did_work = False форсирует паузу — сбой не превратится в
                # busy-loop.
                log.exception("непредвиденный сбой в цикле обработки")
                did_work = False
            if not did_work:
                await asyncio.sleep(poll_interval)
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_runner.py -v`
Expected: PASS, 6 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add api/src/karaoke_api/jobs/runner.py api/tests/test_runner.py
git commit -m "feat(api): фоновый исполнитель задач"
```

---

### Task 7: Загрузка трека

**Files:**
- Create: `api/src/karaoke_api/deps.py`
- Create: `api/src/karaoke_api/main.py`
- Create: `api/tests/test_upload.py`

**Interfaces:**
- Consumes: всё из задач 1–6
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`; объект состояния `AppState` с полями `store`, `storage`, `separator`, `runner`; эндпоинт `POST /api/tracks` → `201 {"track_id": str, "job_id": str}`, ошибки `400 {"error": "unsupported_format"|"too_long"|"too_large"}`; плюс `413 {"error": "too_large"}` от `RejectOversizedBody`, когда размер объявлен в `Content-Length` (400 остаётся за счётчиком байт: chunked и подделанный заголовок).

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_upload.py`:

```python
import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        max_duration_sec=5,
        max_upload_bytes=1_000_000,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _upload(client, path, name="song.wav", mime="audio/wav"):
    with open(path, "rb") as fh:
        return client.post("/api/tracks", files={"file": (name, fh, mime)})


def test_accepts_valid_wav(client, make_wav):
    response = _upload(client, make_wav(duration_sec=1.0))
    assert response.status_code == 201
    body = response.json()
    assert body["track_id"]
    assert body["job_id"]


def test_rejects_too_long(client, make_wav):
    # 5,2 с при 44,1 кГц/стерео/16 бит — это 917 КБ: над лимитом длительности
    # (5 с), но под лимитом размера (1 МБ). Иначе тест поймает too_large и
    # проверит не то, чем назван.
    response = _upload(client, make_wav(duration_sec=5.2))
    assert response.status_code == 400
    assert response.json()["error"] == "too_long"


def test_rejects_non_audio(client, tmp_path):
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all")
    response = _upload(client, junk)
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_format"


def test_rejects_too_large(client, tmp_path, make_wav):
    """Объявленный Content-Length больше лимита — 413 до разбора формы."""
    big = make_wav(name="big.wav", duration_sec=4.0)
    padded = tmp_path / "padded.wav"
    padded.write_bytes(big.read_bytes() + b"\x00" * 1_000_000)
    response = _upload(client, padded)
    assert response.status_code == 413
    assert response.json()["error"] == "too_large"


def test_extension_does_not_grant_access(client, make_wav, tmp_path):
    """Расширение .wav на мусоре не должно проходить."""
    fake = tmp_path / "liar.wav"
    fake.write_bytes(b"still not audio")
    assert _upload(client, fake).json()["error"] == "unsupported_format"


def test_rejects_allowed_but_unlisted_format(client, tmp_path):
    """OGG читается soundfile, но в allowed_formats его нет."""
    import numpy as np
    import soundfile as sf

    ogg = tmp_path / "track.ogg"
    sf.write(ogg, np.zeros((44100, 2), dtype="float32"), 44100, format="OGG")

    response = _upload(client, ogg, name="track.ogg", mime="audio/ogg")
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_format"


def test_filename_traversal_is_contained(client, make_wav, tmp_path):
    """Имя из multipart не должно участвовать в построении пути записи."""
    escaped = tmp_path.parent / "evil.wav"
    if escaped.exists():
        escaped.unlink()

    response = _upload(client, make_wav(duration_sec=1.0),
                       name="../../evil.wav")

    assert response.status_code == 201
    assert not escaped.exists()
    assert not list(tmp_path.parent.rglob("evil.wav"))
```

Последние два теста закрывают границу безопасности загрузки: читаемость файла —
не разрешение на него, а имя файла — не часть пути.

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_upload.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.main'`

- [ ] **Step 3: Написать сборку зависимостей**

Создать `api/src/karaoke_api/deps.py`:

```python
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .gpu import GpuStatus
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .separation.base import StemSeparator
from .separation.fake import FakeSeparator
from .storage.local import LocalStorage


def build_separator(settings: Settings,
                    gpu: GpuStatus | None = None) -> StemSeparator:
    """Собрать разделитель, считаясь с результатом проверки GPU.

    Без gpu DemucsSeparator выбирает устройство сам по
    torch.cuda.is_available(), а тот возвращает True и на сборке без ядер
    под нашу архитектуру — зонд для того и считает настоящую арифметику.
    Игнорировать его вердикт значило бы писать в лог «обработка пойдёт на
    CPU» и всё равно уходить на cuda, роняя каждую задачу на no kernel
    image. Спека (§6) требует ровно обратного: при неудаче — продолжать
    на CPU.
    """
    if settings.separator == "fake":
        return FakeSeparator()
    from .separation.demucs_local import DemucsSeparator

    if gpu is not None and not gpu.available:
        return DemucsSeparator(device="cpu")
    return DemucsSeparator()


@dataclass
class AppState:
    settings: Settings
    store: JobStore
    storage: LocalStorage
    separator: StemSeparator
    runner: JobRunner

    @classmethod
    def build(cls, settings: Settings,
              gpu: GpuStatus | None = None) -> "AppState":
        store = JobStore(settings.db_path)
        storage = LocalStorage(Path(settings.data_dir) / "files")
        separator = build_separator(settings, gpu)
        runner = JobRunner(
            store, storage, separator, Path(settings.data_dir) / "work"
        )
        return cls(settings, store, storage, separator, runner)
```

- [ ] **Step 4: Написать приложение с эндпоинтом загрузки**

Создать `api/src/karaoke_api/main.py`:

```python
import asyncio
import contextlib
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse

from .audio.probe import UnsupportedAudio, probe_audio
from .config import Settings, get_settings
from .deps import AppState
from .jobs.store import new_id

log = logging.getLogger(__name__)


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


class RejectOversizedBody:
    """Отвергает запрос по объявленному Content-Length до разбора формы.

    Счётчик байт внутри обработчика загрузки не ограничивает ничего:
    `file: UploadFile` разрешается как зависимость уже ПОСЛЕ того, как
    FastAPI вызвал `await request.form()` (routing.py: form читается на
    строке 430, зависимости решаются на 481), а Starlette к этому моменту
    вычитал всё тело в SpooledTemporaryFile. `MultiPartParser.max_part_size`
    (1 МБ) применяется только в ветке `if self._current_part.file is None`,
    то есть к обычным полям формы — файловые части пишутся без лимита.
    Неаутентифицированный клиент таким образом кладёт на диск системного
    temp тело любого размера, а лимит срабатывает постфактум.

    Отсюда и ASGI-уровень: это единственная точка отказа ДО разбора формы.
    Заголовок можно подделать, поэтому счётчик в обработчике остаётся
    страховкой, а отсутствующий Content-Length (chunked) загрузку не
    ломает — там работает тот же счётчик.

    Настоящий периметр (nginx/Cloudflare `client_max_body_size`) это не
    заменяет: до приложения запрос всё равно доходит.
    """

    # Multipart-конверт (граница, заголовки части) добавляет к телу сотни
    # байт. Без допуска файл ровно в max_upload_bytes отвергался бы.
    ENVELOPE_SLACK = 8192

    def __init__(self, app, max_upload_bytes: int) -> None:
        self.app = app
        self._limit = max_upload_bytes + self.ENVELOPE_SLACK

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and self._declared_over_limit(scope):
            response = _error("too_large", status=413)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _declared_over_limit(self, scope) -> bool:
        for name, value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            try:
                return int(value) > self._limit
            except ValueError:
                # Мусор в заголовке — не наше дело его валидировать,
                # дальше по стеку тело всё равно посчитает счётчик.
                return False
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState.build(settings)
        app.state.karaoke = state

        orphans = state.store.fail_orphans()
        if orphans:
            log.warning("помечено упавшими незавершённых задач: %d", orphans)

        task = asyncio.create_task(state.runner.run_forever())
        try:
            yield
        finally:
            state.runner.stop()
            task.cancel()
            # cancel() только планирует отмену. Без await соединение SQLite
            # закроется раньше, чем воркер домотает свой путь размотки.
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # Но и await недостаточно: отмена снимает лишь ожидание
            # asyncio.to_thread, рабочий поток с текущей задачей продолжает
            # считать. Закрыть базу под ним значит уронить его на
            # set_stage/finish и оставить задачу в running навсегда.
            finished = await asyncio.to_thread(
                state.runner.wait_until_idle, settings.shutdown_wait_sec
            )
            if not finished:
                log.warning(
                    "задача не досчитала за %s с — закрываю базу, её "
                    "результат будет потерян", settings.shutdown_wait_sec,
                )
            state.store.close()

    app = FastAPI(title="Karaoke API", lifespan=lifespan)
    app.add_middleware(
        RejectOversizedBody, max_upload_bytes=settings.max_upload_bytes
    )

    @app.post("/api/tracks", status_code=201)
    async def upload_track(request: Request, file: UploadFile):
        state: AppState = request.app.state.karaoke
        limits = state.settings

        with tempfile.TemporaryDirectory() as tmp:
            # Имя из multipart полностью контролируется клиентом: "../../x"
            # увело бы запись за пределы каталога ещё до всякой валидации.
            # Кладём под фиксированным именем, filename оставляем метаданными.
            staged = Path(tmp) / "upload"
            size = 0
            with staged.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limits.max_upload_bytes:
                        return _error("too_large")
                    out.write(chunk)

            try:
                info = probe_audio(staged)
            except UnsupportedAudio:
                return _error("unsupported_format")

            if info.duration_sec > limits.max_duration_sec:
                return _error("too_long")

            # info.format — имя контейнера от libsndfile, allowed_formats —
            # список пользовательских форматов. Для семейства wav это разные
            # словари, их надо свести, иначе обычный WAVEX-файл от редактора
            # получает «формат не поддерживается».
            fmt = normalize_format(info.format)
            if fmt not in limits.allowed_formats:
                # soundfile читает два десятка форматов сверх нашего списка
                # (OGG, AIFF, CAF...). Читаемость — не то же самое, что
                # разрешённость.
                return _error("unsupported_format")

            # Идентификатор выдаётся до вставки: ключ строится из него, и
            # запись попадает в базу сразу целиком.
            track_id = new_id()
            key = f"tracks/{track_id}/original.{fmt}"
            state.storage.store_file(key, staged)
            state.store.create_track(
                track_id, file.filename or "upload", key, info.duration_sec
            )

        job_id = state.store.create_job(track_id)
        return {"track_id": track_id, "job_id": job_id}

    return app


app = create_app()
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_upload.py -v`
Expected: PASS, 7 тестов

- [ ] **Step 6: Прогнать весь набор**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS, все тесты задач 1–7

- [ ] **Step 7: Коммит**

```bash
cd .. && git add api/src/karaoke_api api/tests/test_upload.py
git commit -m "feat(api): загрузка трека с валидацией"
```

---

### Task 8: Статус задачи

**Files:**
- Modify: `api/src/karaoke_api/main.py` (добавить эндпоинт)
- Create: `api/tests/test_job_endpoint.py`

**Interfaces:**
- Consumes: `AppState`, `JobStore` из задач 5 и 7
- Produces: `GET /api/jobs/{job_id}` → `200 {"status", "stage", "progress", "error", "result"}`, `404 {"error": "not_found"}`.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_job_endpoint.py`:

```python
import time

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _upload(client, path):
    with open(path, "rb") as fh:
        return client.post("/api/tracks",
                           files={"file": ("s.wav", fh, "audio/wav")}).json()


def _wait_done(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"задача не завершилась за {timeout} с")


def test_unknown_job_is_404(client):
    response = client.get("/api/jobs/deadbeef")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_job_reaches_done_with_stems(client, make_wav):
    ids = _upload(client, make_wav(duration_sec=1.0))
    body = _wait_done(client, ids["job_id"])

    assert body["status"] == "done"
    assert body["stage"] is None
    assert body["progress"] == 1.0
    assert body["error"] is None
    assert set(body["result"]["stems"]) == {"vocals", "no_vocals"}


def test_response_shape_is_stable(client, make_wav):
    ids = _upload(client, make_wav(duration_sec=1.0))
    body = client.get(f"/api/jobs/{ids['job_id']}").json()
    assert set(body) == {"status", "stage", "progress", "error", "result"}


def test_running_job_reports_stage_as_plain_string(client):
    state = client.app.state.karaoke

    # Фоновый цикл остановлен, иначе он перехватит задачу и доведёт её до done.
    # stop() проверяется в начале итерации, поэтому ждём чуть дольше интервала
    # опроса — так тест детерминирован, а не «обычно успевает».
    state.runner.stop()
    time.sleep(0.6)

    track_id = new_id()
    state.store.create_track(
        track_id, "s.wav", f"tracks/{track_id}/original.wav", 1.0
    )
    job_id = state.store.create_job(track_id)
    state.store.claim_next()
    state.store.set_stage(job_id, Stage.SEPARATING, 0.42)

    body = client.get(f"/api/jobs/{job_id}").json()

    assert body["status"] == "running"
    assert body["stage"] == "separating"
    assert isinstance(body["stage"], str) and not isinstance(body["stage"], Stage)
    assert body["progress"] == pytest.approx(0.42)
```

Последний тест закрывает единственную ошибкоопасную строку эндпоинта. Пара
`isinstance` здесь несущая: `Stage` наследуется от `str`, поэтому утёкший
элемент перечисления прошёл бы и сравнение со строкой, и первую проверку —
ловит только отрицательная.

Импорты теста: `time`, `pytest`, `from karaoke_api.jobs.models import Stage`,
`from karaoke_api.jobs.store import new_id`.

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_job_endpoint.py -v`
Expected: FAIL — 404 на всех запросах, потому что маршрут не объявлен

- [ ] **Step 3: Добавить эндпоинт**

В `api/src/karaoke_api/main.py` вставить перед `return app`:

```python
    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        state: AppState = request.app.state.karaoke
        job = state.store.get_job(job_id)
        if job is None:
            return _error("not_found", status=404)
        return {
            "status": job.status.value,
            "stage": job.stage.value if job.stage else None,
            "progress": job.progress,
            "error": job.error_message,
            "result": job.result,
        }
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_job_endpoint.py -v`
Expected: PASS, 4 теста

- [ ] **Step 5: Коммит**

```bash
cd .. && git add api/src/karaoke_api/main.py api/tests/test_job_endpoint.py
git commit -m "feat(api): эндпоинт статуса задачи"
```

---

### Task 9: Выдача дорожек с поддержкой Range

**Files:**
- Create: `api/src/karaoke_api/ranges.py`
- Modify: `api/src/karaoke_api/main.py` (добавить эндпоинт)
- Create: `api/tests/test_ranges.py`
- Create: `api/tests/test_stems_endpoint.py`

**Interfaces:**
- Consumes: `Storage` (Task 2), `AppState` (Task 7)
- Produces: `parse_range(header: str | None, size: int) -> tuple[int, int] | None` (возвращает `(start, end)` включительно); `GET /api/tracks/{track_id}/stems/{kind}` → `200` или `206` с `Content-Range`, `404 {"error": "not_found"}`, `416` при недопустимом диапазоне.

- [ ] **Step 1: Написать падающий тест разбора заголовка**

Создать `api/tests/test_ranges.py`:

```python
import pytest

from karaoke_api.ranges import parse_range


def test_absent_header_returns_none():
    assert parse_range(None, 1000) is None


def test_simple_range():
    assert parse_range("bytes=0-99", 1000) == (0, 99)


def test_open_ended_range_clamps_to_size():
    assert parse_range("bytes=500-", 1000) == (500, 999)


def test_suffix_range_counts_from_end():
    assert parse_range("bytes=-100", 1000) == (900, 999)


def test_end_past_size_is_clamped():
    assert parse_range("bytes=900-5000", 1000) == (900, 999)


def test_start_past_size_raises():
    with pytest.raises(ValueError):
        parse_range("bytes=2000-", 1000)


def test_reversed_range_raises():
    with pytest.raises(ValueError):
        parse_range("bytes=500-100", 1000)


def test_unsupported_unit_returns_none():
    assert parse_range("items=0-10", 1000) is None
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_ranges.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.ranges'`

- [ ] **Step 3: Написать разбор**

Создать `api/src/karaoke_api/ranges.py`:

```python
def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Разобрать заголовок Range. Возвращает (start, end) включительно.

    None — заголовка нет или единица измерения не bytes (отдаём файл целиком).
    ValueError — диапазон синтаксически верен, но недостижим: ответ 416.
    """
    if not header:
        return None

    header = header.strip()
    if not header.startswith("bytes="):
        return None

    spec = header[len("bytes="):].split(",")[0].strip()
    start_raw, _, end_raw = spec.partition("-")

    if not start_raw:
        if not end_raw:
            raise ValueError(f"пустой диапазон: {header!r}")
        length = int(end_raw)
        if length <= 0:
            raise ValueError(f"недопустимая длина суффикса: {header!r}")
        start = max(0, size - length)
        return start, size - 1

    start = int(start_raw)
    if start >= size:
        raise ValueError(f"начало за пределами файла: {header!r}")

    end = int(end_raw) if end_raw else size - 1
    end = min(end, size - 1)
    if end < start:
        raise ValueError(f"конец раньше начала: {header!r}")

    return start, end
```

- [ ] **Step 4: Запустить тесты разбора**

Run: `.venv/Scripts/python -m pytest tests/test_ranges.py -v`
Expected: PASS, 8 тестов

- [ ] **Step 5: Написать падающий тест эндпоинта**

Создать `api/tests/test_stems_endpoint.py`:

```python
import time

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def ready_track(client, make_wav):
    with open(make_wav(duration_sec=1.0), "rb") as fh:
        ids = client.post("/api/tracks",
                          files={"file": ("s.wav", fh, "audio/wav")}).json()
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/jobs/{ids['job_id']}").json()["status"] == "done":
            return ids["track_id"]
        time.sleep(0.05)
    raise AssertionError("задача не завершилась")


def test_full_download(client, ready_track):
    response = client.get(f"/api/tracks/{ready_track}/stems/no_vocals")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == int(response.headers["content-length"])


def test_range_request_returns_206(client, ready_track):
    response = client.get(
        f"/api/tracks/{ready_track}/stems/no_vocals",
        headers={"Range": "bytes=0-99"},
    )
    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.headers["content-range"].startswith("bytes 0-99/")


def test_unsatisfiable_range_returns_416(client, ready_track):
    response = client.get(
        f"/api/tracks/{ready_track}/stems/no_vocals",
        headers={"Range": "bytes=99999999-"},
    )
    assert response.status_code == 416


def test_unknown_kind_is_404(client, ready_track):
    assert client.get(f"/api/tracks/{ready_track}/stems/drums").status_code == 404


def test_unknown_track_is_404(client):
    assert client.get("/api/tracks/nope/stems/vocals").status_code == 404
```

- [ ] **Step 6: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_stems_endpoint.py -v`
Expected: FAIL — маршрут не объявлен

- [ ] **Step 7: Добавить эндпоинт**

В `api/src/karaoke_api/main.py` добавить импорт
`from fastapi.responses import Response, StreamingResponse` рядом с
`JSONResponse`, импорт `from .ranges import parse_range`, и вставить перед
`return app`:

```python
    _STEM_KINDS = ("vocals", "no_vocals")

    @app.get("/api/tracks/{track_id}/stems/{kind}")
    async def get_stem(request: Request, track_id: str, kind: str):
        state: AppState = request.app.state.karaoke
        if kind not in _STEM_KINDS:
            return _error("not_found", status=404)

        # Хранилище — не источник истины о том, что трек существует. Файлы
        # могут пережить удаление строки (гонка с работающей задачей, сбой
        # уборки), и отдавать их после этого нельзя: для клиента трек удалён.
        if state.store.get_track(track_id) is None:
            return _error("not_found", status=404)

        key = f"tracks/{track_id}/stems/{kind}.wav"
        if not state.storage.exists(key):
            return _error("not_found", status=404)

        total = state.storage.size(key)
        try:
            rng = parse_range(request.headers.get("range"), total)
        except ValueError:
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{total}"}
            )

        start, end = rng if rng else (0, total - 1)
        length = end - start + 1

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        if rng is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{total}"

        # Дорожка весит десятки мегабайт: отдаём потоком, а не одним куском
        # в памяти на каждый запрос.
        return StreamingResponse(
            state.storage.iter_range(key, start, length),
            status_code=206 if rng else 200,
            media_type="audio/wav",
            headers=headers,
        )
```

- [ ] **Step 8: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_stems_endpoint.py -v`
Expected: PASS, 5 тестов

- [ ] **Step 9: Коммит**

```bash
cd .. && git add api/src/karaoke_api api/tests/test_ranges.py api/tests/test_stems_endpoint.py
git commit -m "feat(api): выдача дорожек с поддержкой Range"
```

---

### Task 10: Удаление трека и автоочистка по TTL

**Files:**
- Create: `api/src/karaoke_api/cleanup.py`
- Modify: `api/src/karaoke_api/main.py` (эндпоинт удаления и запуск уборки)
- Create: `api/tests/test_cleanup.py`

**Interfaces:**
- Consumes: `JobStore` (Task 5), `Storage` (Task 2)
- Produces: `purge_expired(store, storage, ttl_hours: int, now: datetime | None = None) -> int`; `DELETE /api/tracks/{track_id}` → `204`, `404` если трека нет.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_cleanup.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from karaoke_api.cleanup import purge_expired
from karaoke_api.config import Settings
from karaoke_api.jobs.store import JobStore, new_id
from karaoke_api.main import create_app
from karaoke_api.storage.local import LocalStorage


@pytest.fixture
def wiring(tmp_path, make_wav):
    store = JobStore(tmp_path / "db.sqlite")
    storage = LocalStorage(tmp_path / "store")
    track_id = new_id()
    key = f"tracks/{track_id}/original.wav"
    storage.store_file(key, make_wav(duration_sec=0.5))
    store.create_track(track_id, "s.wav", key, 0.5)
    return store, storage, track_id


def test_fresh_track_survives(wiring):
    store, storage, track_id = wiring
    assert purge_expired(store, storage, ttl_hours=24) == 0
    assert store.get_track(track_id) is not None


def test_expired_track_is_removed(wiring):
    store, storage, track_id = wiring
    future = datetime.now(timezone.utc) + timedelta(hours=25)

    assert purge_expired(store, storage, ttl_hours=24, now=future) == 1

    assert store.get_track(track_id) is None
    assert not storage.exists(f"tracks/{track_id}/original.wav")


def test_delete_endpoint_removes_track(tmp_path, make_wav):
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

        assert client.delete(f"/api/tracks/{ids['track_id']}").status_code == 204
        assert client.get(f"/api/jobs/{ids['job_id']}").status_code == 404


def test_delete_unknown_track_is_404(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        assert client.delete("/api/tracks/nope").status_code == 404
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.cleanup'`

- [ ] **Step 3: Написать уборку**

Создать `api/src/karaoke_api/cleanup.py`:

```python
import logging
from datetime import datetime, timedelta, timezone

from .jobs.store import JobStore
from .storage.base import Storage

log = logging.getLogger(__name__)


def purge_expired(store: JobStore, storage: Storage, ttl_hours: int,
                  now: datetime | None = None) -> int:
    """Удалить треки старше TTL вместе с файлами. Возвращает число удалённых."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    removed = 0
    for track_id in store.list_expired_tracks(cutoff):
        # delete_prefix пробрасывает ошибки (см. задачу 2). На Windows файл,
        # открытый работающей задачей, не удалится — и без этой обёртки один
        # такой трек оборвал бы весь проход уборки.
        try:
            storage.delete_prefix(f"tracks/{track_id}")
            store.delete_track(track_id)
        except Exception:
            log.exception("не удалось удалить трек %s, продолжаю", track_id)
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
```

- [ ] **Step 4: Добавить эндпоинт и периодическую уборку**

В `api/src/karaoke_api/main.py` добавить импорт `from .cleanup import purge_expired, purge_orphan_track_dirs` и вставить перед `return app`:

```python
    @app.delete("/api/tracks/{track_id}", status_code=204)
    async def delete_track(request: Request, track_id: str):
        state: AppState = request.app.state.karaoke
        if state.store.get_track(track_id) is None:
            return _error("not_found", status=404)
        try:
            state.storage.delete_prefix(f"tracks/{track_id}")
        except Exception:
            # На Windows файл, занятый работающей задачей, не удаляется
            # (WinError 32) — штатный сценарий, а не сбой сервиса, и отвечать
            # на него голым 500 без кода нечестно. Та же защита, что в цикле
            # автоочистки. Строку трека оставляем намеренно: без неё файлы
            # стали бы сиротами, а так их подберёт уборка по TTL.
            log.exception("не удалось удалить файлы трека %s", track_id)
            return _error("delete_failed", status=503)
        state.store.delete_track(track_id)
        return Response(status_code=204)
```

В функции `lifespan` после `orphans = state.store.fail_orphans()` добавить:

```python
        purge_expired(state.store, state.storage, settings.file_ttl_hours)
        # Сверка каталогов с таблицей: файлы трека, чью строку уже удалили,
        # не увидит больше ни одна уборка.
        purge_orphan_track_dirs(state.store, state.storage)

        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                # Как и цикл обработки: одно вылетевшее исключение иначе
                # навсегда останавливает уборку при живом HTTP.
                try:
                    await asyncio.to_thread(
                        purge_expired, state.store, state.storage,
                        settings.file_ttl_hours,
                    )
                except Exception:
                    log.exception("сбой автоочистки")

        cleanup_task = asyncio.create_task(_cleanup_loop())
```

и в блоке `finally` добавить `cleanup_task.cancel()` рядом с `task.cancel()`.

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_cleanup.py -v`
Expected: PASS, 4 теста

- [ ] **Step 6: Прогнать весь набор**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS, все быстрые тесты

- [ ] **Step 7: Коммит**

```bash
cd .. && git add api/src/karaoke_api api/tests/test_cleanup.py
git commit -m "feat(api): удаление трека и автоочистка по TTL"
```

---

### Task 11: Проверка GPU при старте

**Files:**
- Create: `api/src/karaoke_api/gpu.py`
- Modify: `api/src/karaoke_api/main.py` (вызов при старте, эндпоинт здоровья)
- Create: `api/tests/test_gpu_check.py`

**Interfaces:**
- Consumes: ничего
- Produces: `GpuStatus` (frozen dataclass: `available: bool`, `device_name: str | None`, `reason: str | None`, `hint: str | None`); `check_gpu() -> GpuStatus`; `GET /api/health` → `{"gpu": {...}, "separator": str}`.

- [ ] **Step 1: Написать падающий тест**

Создать `api/tests/test_gpu_check.py`:

```python
import sys
import types

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.gpu import GpuStatus, check_gpu
from karaoke_api.main import create_app


def _fake_torch(*, cuda_available: bool, raises: Exception | None = None,
                capability=(12, 0), name="RTX 5060"):
    module = types.ModuleType("torch")

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return cuda_available

        @staticmethod
        def get_device_name(idx: int = 0) -> str:
            return name

        @staticmethod
        def get_device_capability(idx: int = 0):
            return capability

    def _zeros(_n, device=None):
        if raises is not None:
            raise raises
        return object()

    module.cuda = _Cuda
    module.zeros = _zeros
    return module


def test_missing_torch_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    status = check_gpu()
    assert isinstance(status, GpuStatus)
    assert status.available is False
    assert "torch" in status.reason.lower()


def test_no_cuda_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=False))
    status = check_gpu()
    assert status.available is False
    assert "cuda" in status.reason.lower()


def test_kernel_image_failure_gives_cu128_hint(monkeypatch):
    err = RuntimeError("CUDA error: no kernel image is available for execution")
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True, raises=err))
    status = check_gpu()
    assert status.available is False
    assert "cu128" in status.hint


def test_working_gpu_is_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True))
    status = check_gpu()
    assert status.available is True
    assert status.device_name == "RTX 5060"
    assert status.reason is None


def test_health_endpoint_exposes_gpu(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/health").json()
        assert "gpu" in body
        assert "available" in body["gpu"]
        assert body["separator"] == "fake"
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python -m pytest tests/test_gpu_check.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'karaoke_api.gpu'`

- [ ] **Step 3: Написать проверку**

Создать `api/src/karaoke_api/gpu.py`:

```python
import importlib
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CU128_HINT = (
    "Установите сборку PyTorch под CUDA 12.8 или новее: "
    "pip install torch --index-url https://download.pytorch.org/whl/cu128 "
    "(RTX 5060 — архитектура Blackwell, sm_120, обычная сборка её не знает)"
)


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    device_name: str | None = None
    reason: str | None = None
    hint: str | None = None


def check_gpu() -> GpuStatus:
    """Проверить GPU настоящим вычислением, а не только is_available().

    is_available() возвращает True и на несовместимой сборке — ошибка
    вылезает лишь при первом вычислении. zeros() тоже не годится: заполнение
    нулями свежего тензора реализовано через cudaMemsetAsync — операцию
    уровня драйвера, которая пройдёт и без ядер под нашу архитектуру. Нужна
    настоящая арифметика, а .item() ещё и синхронизирует: без него
    асинхронная ошибка запуска ядра может не всплыть на месте вызова.

    Всё тело после импорта обёрнуто в try — сломанный драйвер способен
    уронить даже is_available(), а по контракту задачи проверка не должна
    валить старт приложения ни при каких обстоятельствах.
    """
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return GpuStatus(False, reason=f"torch не импортируется: {exc}",
                         hint=CU128_HINT)

    try:
        if not torch.cuda.is_available():
            return GpuStatus(False, reason="CUDA недоступна", hint=NO_GPU_HINT)

        probe = (torch.ones(8, device="cuda") * 2).sum().item()
        if probe != 16:
            return GpuStatus(
                False,
                reason=f"пробное вычисление дало {probe}, ожидалось 16",
                hint=CU128_HINT,
            )

        try:
            device_name = torch.cuda.get_device_name(0)
        except Exception:
            # Само вычисление сработало — GPU реально доступен. Не проваливать
            # весь проб из-за того, что не удалось узнать только имя устройства.
            device_name = None

        return GpuStatus(True, device_name=device_name)
    except Exception as exc:
        return GpuStatus(False, reason=f"проверка GPU упала: {exc}",
                         hint=CU128_HINT)
```

- [ ] **Step 4: Подключить в приложение**

В `api/src/karaoke_api/main.py` добавить импорт `from .gpu import check_gpu`, в `lifespan` — В САМОЕ НАЧАЛО, до `AppState.build(...)`, вставить:

```python
        # Проверка идёт до сборки состояния: её вердикт выбирает устройство
        # разделителя. Раньше сепаратор строился первым и решал сам по
        # torch.cuda.is_available() — то есть уходил на cuda даже тогда,
        # когда зонд уже установил, что ядер под эту архитектуру нет.
        gpu = check_gpu()
        app.state.gpu = gpu
        if gpu.available:
            log.info("GPU готов: %s", gpu.device_name)
        else:
            log.warning("GPU недоступен (%s). Обработка пойдёт на CPU и будет "
                        "в десятки раз медленнее. %s", gpu.reason, gpu.hint)
```

и передать вердикт в сборку состояния: `state = AppState.build(settings, gpu)`.

и вставить перед `return app`:

```python
    @app.get("/api/health")
    async def health(request: Request):
        gpu = request.app.state.gpu
        return {
            "gpu": {
                "available": gpu.available,
                "device_name": gpu.device_name,
                "reason": gpu.reason,
                "hint": gpu.hint,
            },
            "separator": settings.separator,
        }
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python -m pytest tests/test_gpu_check.py -v`
Expected: PASS, 5 тестов

- [ ] **Step 6: Коммит**

```bash
cd .. && git add api/src/karaoke_api api/tests/test_gpu_check.py
git commit -m "feat(api): проверка GPU при старте с подсказкой про cu128"
```

---

### Task 12: Настоящий demucs и измерение времени

**Files:**
- Create: `api/src/karaoke_api/separation/demucs_local.py`
- Modify: `api/pyproject.toml` (зависимости demucs и torch)
- Create: `api/tests/test_demucs_slow.py`
- Create: `api/README.md`

**Interfaces:**
- Consumes: `StemSeparator`, `SeparationResult`, `ProgressCallback` (Task 4)
- Produces: класс `DemucsSeparator(model_name: str = "htdemucs", device: str | None = None)`, реализующий `StemSeparator`; поле `.device` со значением `"cuda"` или `"cpu"`.

- [ ] **Step 1: Установить torch под cu128 и demucs**

```bash
cd api
.venv/Scripts/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python -m pip install demucs
```

Проверить, что GPU виден:

```bash
.venv/Scripts/python -c "import torch; print(torch.cuda.is_available()); print(torch.zeros(8, device='cuda').sum())"
```

Expected: `True` и `tensor(0., device='cuda:0')`. Если падает с `no kernel image` — сборка не та, вернуться к индексу cu128 и переустановить.

- [ ] **Step 2: Зафиксировать зависимости в `pyproject.toml`**

В `[project.optional-dependencies]` добавить группу:

```toml
gpu = ["torch>=2.7", "torchaudio>=2.7", "demucs>=4.0.1"]
```

- [ ] **Step 3: Написать реализацию**

Создать `api/src/karaoke_api/separation/demucs_local.py`:

**Отступление от исходного текста (решение человека-партнёра, зафиксировано
при выполнении задачи 12):** код ниже читает аудио через `torchaudio.load`.
В установленной версии torchaudio (2.11) это больше не работает — вызов
делегирует в `torchaudio.load_with_torchcodec`, который требует пакет
`torchcodec`; ставить его не будем (падает
`ImportError: TorchCodec is required for load_with_torchcodec`). Заменено на
чтение через `soundfile` — он уже прямая зависимость проекта
(`karaoke_api/audio/probe.py` читает им же). `soundfile.read` отдаёт
`(frames, channels)`, поэтому нужна транспозиция в `(channels, frames)` и
`dtype="float32"` с `always_2d=True`, чтобы моно не приезжало одномерным.
`torchaudio.functional.resample` и запись через `demucs.audio.save_audio`
остаются без изменений — это чистые тензорные операции и запись `.wav`
собственным кодом demucs, кодеков не касаются.

**Второй круг ревью (та же задача 12) добавил ещё два отступления,
предписанных планом изначально (тот же пробел был и в коде брифа), плюс одно
чисто структурное — синхронизирую их сюда же:**
- вход с числом каналов больше двух (например, 5.1 во flac) не был обработан
  — `apply_model` падал бы по форме тензора; добавлено сведение в моно перед
  дублированием в стерео;
- откат на CPU при нехватке видеопамяти не восстанавливал устройство модели
  в `finally` (терялось при отказе и самого CPU-прохода) и был формально
  достижим при `self.device == "cpu"`, где восстанавливать нечего — вынесено
  в отдельный метод `_apply_with_fallback` с `try/finally` и явным пропуском
  ветки отката вне CUDA.

```python
import logging
from pathlib import Path

import soundfile as sf
import torch
import torchaudio
from demucs.apply import apply_model
from demucs.audio import save_audio
from demucs.pretrained import get_model

from .base import ProgressCallback, SeparationResult

log = logging.getLogger(__name__)


def _load_wav(source: Path) -> tuple[torch.Tensor, int]:
    """Прочитать аудио через soundfile, вернуть тензор (channels, frames)."""
    data, sample_rate = sf.read(str(source), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T).contiguous()
    return wav, sample_rate


class DemucsSeparator:
    """Локальное разделение через demucs. Модель грузится один раз.

    Работает в режиме двух дорожек: вокал и всё остальное. Внутри модель
    считает четыре источника и складывает три из них в аккомпанемент.
    """

    def __init__(self, model_name: str = "htdemucs",
                 device: str | None = None) -> None:
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            log.info("загружаю модель %s на %s", self._model_name, self.device)
            self._model = get_model(self._model_name).to(self.device).eval()
        return self._model

    def _apply_with_fallback(self, model, wav: torch.Tensor) -> torch.Tensor:
        """Прогнать модель, на нехватке видеопамяти — откатиться на CPU.

        При self.device != "cuda" ветку отката пропускаем явно: откатываться
        уже некуда, а OutOfMemoryError там взяться неоткуда. Восстановление
        устройства модели — в finally: если сорвётся и сам CPU-проход,
        self._model не должен навсегда осесть на CPU, пока self.device
        продолжает утверждать "cuda".
        """
        if self.device != "cuda":
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0]
        try:
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0]
        except torch.cuda.OutOfMemoryError as exc:
            log.warning("нехватка видеопамяти, повторяю на CPU: %s", exc)
            torch.cuda.empty_cache()
            model.to("cpu")
            try:
                with torch.no_grad():
                    return apply_model(model, wav[None], device="cpu",
                                       progress=False)[0]
            finally:
                model.to(self.device)

    def separate(self, source: Path, out_dir: Path,
                 on_progress: ProgressCallback) -> SeparationResult:
        on_progress("loading", 0.0)
        model = self._ensure_model()

        wav, sample_rate = _load_wav(source)
        if wav.shape[0] > 2:
            # Больше двух каналов (например, 5.1 в flac) — demucs/apply_model
            # ждёт ровно audio_channels (2 для htdemucs) и упадёт по форме
            # тензора. Сводим в моно и дублируем, как и одноканальный вход.
            wav = wav.mean(0, keepdim=True)
        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        if sample_rate != model.samplerate:
            wav = torchaudio.functional.resample(wav, sample_rate,
                                                 model.samplerate)

        reference = wav.mean(0)
        wav = (wav - reference.mean()) / (reference.std() + 1e-8)

        on_progress("separating", 0.1)
        sources = self._apply_with_fallback(model, wav)

        sources = sources * (reference.std() + 1e-8) + reference.mean()
        stems = dict(zip(model.sources, sources))

        vocals_path = Path(out_dir) / "vocals.wav"
        no_vocals_path = Path(out_dir) / "no_vocals.wav"

        vocals = stems["vocals"]
        no_vocals = sum(t for name, t in stems.items() if name != "vocals")

        # on_progress("writing", ...) — только здесь, а не до сборки стемов:
        # инференс уже завершён и тензоры посчитаны, дальше только запись на
        # диск. Раньше (как в первой версии этого блока) стадия "writing"
        # сигналилась до сложения трёх тензоров в аккомпанемент — прогресс-бар
        # был бы нечестным. Тот же порядок бага был в FakeSeparator (Task 4),
        # там он безвреден: запись — это копирование файла, а не тяжёлая
        # стадия.
        on_progress("writing", 0.9)
        save_audio(vocals, str(vocals_path), model.samplerate)
        save_audio(no_vocals, str(no_vocals_path), model.samplerate)

        return SeparationResult(vocals=vocals_path, no_vocals=no_vocals_path)
```

- [ ] **Step 4: Написать медленный измерительный тест**

Создать `api/tests/test_demucs_slow.py`:

**Второй круг ревью задачи 12 указал, что исходный тест ниже проверял только
факт существования файлов, длительность и число каналов — ни одно из этих
утверждений не ловит сломанную денормировку, потерянную транспозицию или
перепутанный порядок каналов: на выходе всё равно получился бы 30-секундный
стерео-WAV. Усилено тремя проверками и отдельным тестом:**
- `separator.device == "cuda"` в начале — маркер `slow` заявляет GPU, тест не
  должен молча превращаться в CPU-замер, который легко прочитать как
  GPU-число;
- `sample_rate == 44100` на обеих выходных дорожках и явная проверка, что
  `vocals` и `no_vocals` не побитово идентичны;
- инвариант реконструкции: `vocals + no_vocals` — это сумма всех четырёх
  источников htdemucs и должна приближённо восстанавливать исходный микс.
  Относительная ошибка (RMS разности к RMS микса), измеренная на настоящих
  прогонах на RTX 5060, — 0,006–0,010; порог в тесте взят с запасом,
  `0.05`;
- отдельный тест `test_load_wav_preserves_channel_order` на файле с заведомо
  разными левым и правым каналом (фикстура `make_wav` пишет одинаковый синус
  в оба канала и не ловит перепутанную ось после `.T`). Он тоже импортирует
  `torch`/`demucs`, поэтому живёт в этом же файле.

**Финальное ревью ветки сняло с файла модульный `pytestmark`.** Импорты
уровня модуля выполняются на этапе СБОРКИ, а `-m 'not slow'` фильтрует уже
после неё — то есть без `pytest.importorskip` весь быстрый набор падал
ошибкой сбора на машине без gpu-группы, вопреки ограничению плана «быстрые
тесты не требуют GPU». `exc_type=ImportError` вместо умолчания
`ModuleNotFoundError`: на Windows торч бывает не отсутствующим, а сломанным
(не подхватились DLL CUDA). Маркер `slow` оставлен только на
`test_separates_real_audio_and_reports_timing`: порядок каналов и оба теста
отката настоящей модели не требуют, они бьют по структурным свойствам
(`calls == ["cpu", "cuda"]`), и именно этот класс регрессий быстрый набор
обязан ловить.

```python
import time

import numpy as np
import pytest
import soundfile as sf

torch = pytest.importorskip("torch", exc_type=ImportError)
pytest.importorskip("demucs", exc_type=ImportError)

from karaoke_api.audio.probe import probe_audio  # noqa: E402
from karaoke_api.separation import demucs_local  # noqa: E402
from karaoke_api.separation.demucs_local import (  # noqa: E402
    DemucsSeparator,
    _load_wav,
)

# Маркер slow — только на тесте с настоящей моделью, см. выше.

MAX_RECONSTRUCTION_RELATIVE_ERROR = 0.05


def _write_asymmetric_stereo(path, duration_sec: float = 0.2,
                             sample_rate: int = 44100):
    frames = int(duration_sec * sample_rate)
    left = np.full(frames, 0.5, dtype=np.float32)
    right = np.full(frames, -0.5, dtype=np.float32)
    data = np.stack([left, right], axis=1)
    sf.write(str(path), data, sample_rate)
    return path


def test_load_wav_preserves_channel_order(tmp_path):
    source = _write_asymmetric_stereo(tmp_path / "lr.wav")
    wav, sample_rate = _load_wav(source)

    assert sample_rate == 44100
    assert wav.shape[0] == 2
    assert wav[0].mean().item() == pytest.approx(0.5, abs=1e-3)
    assert wav[1].mean().item() == pytest.approx(-0.5, abs=1e-3)


@pytest.mark.slow
def test_separates_real_audio_and_reports_timing(make_wav, tmp_path, capsys):
    """Проверяет интеграцию и печатает фактическое время обработки.

    Это число закрывает допущение «25 секунд» из раздела 4.5
    контекстного документа — главную дыру в юнит-экономике.
    """
    duration = 30.0
    source = make_wav(duration_sec=duration, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()

    separator = DemucsSeparator()
    assert separator.device == "cuda"

    started = time.perf_counter()
    result = separator.separate(source, out, lambda stage, pct: None)
    elapsed = time.perf_counter() - started

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()

    vocals_info = probe_audio(result.vocals)
    no_vocals_info = probe_audio(result.no_vocals)
    assert abs(no_vocals_info.duration_sec - duration) < 0.5
    assert no_vocals_info.channels == 2
    assert vocals_info.sample_rate == 44100
    assert no_vocals_info.sample_rate == 44100

    source_wav, _ = sf.read(str(source), dtype="float32", always_2d=True)
    vocals_wav, _ = sf.read(str(result.vocals), dtype="float32", always_2d=True)
    no_vocals_wav, _ = sf.read(str(result.no_vocals), dtype="float32",
                               always_2d=True)
    assert not np.array_equal(vocals_wav, no_vocals_wav)

    frames = min(len(source_wav), len(vocals_wav), len(no_vocals_wav))
    recon = vocals_wav[:frames] + no_vocals_wav[:frames]
    mix = source_wav[:frames]
    diff_rms = float(np.sqrt(np.mean((recon - mix) ** 2)))
    mix_rms = float(np.sqrt(np.mean(mix ** 2)))
    relative_error = diff_rms / mix_rms

    ratio = elapsed / duration
    with capsys.disabled():
        print(
            f"\n=== ЗАМЕР ===\n"
            f"устройство:        {separator.device}\n"
            f"длительность:      {duration:.1f} с\n"
            f"обработка заняла:  {elapsed:.1f} с\n"
            f"на 3,5-мин трек:   {ratio * 210:.1f} с (экстраполяция)\n"
            f"ошибка реконстр.:  {relative_error:.4f}\n"
            f"=============="
        )

    assert relative_error < MAX_RECONSTRUCTION_RELATIVE_ERROR
```

- [ ] **Step 5: Запустить медленный тест**

Run: `.venv/Scripts/python -m pytest tests/test_demucs_slow.py -v -m slow -s`
Expected: PASS, в выводе блок «ЗАМЕР» с фактическим временем

- [ ] **Step 6: Убедиться, что быстрые тесты не задеты**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS, медленный тест пропущен (`deselected`)

- [ ] **Step 7: Записать замер в контекстный документ**

Открыть `karaoke-context.md`, раздел 4.5, таблицу «Допущения». Заменить строку про время обработки фактическим значением из вывода теста и убрать пометку о том, что число не замерено. Если фактическое время отличается от 25 с более чем на треть — пересчитать таблицы «Сравнение путей» и «Что это даёт тарифу» в 4.6.

**Уточнение, зафиксированное при выполнении задачи 12 (решение координатора):**
таблица допущений построена на RTX 4090, а замер физически возможен только
на доступном железе — RTX 5060 (заметно слабее). Молча подставить число с
5060 в модель себестоимости 4090 значило бы соврать в расчёте, поэтому в
документе явно указано железо замера, а денежные таблицы пересчитываются
только если замер на 5060 превысит 25 с больше чем на треть (~33 с) — раз
карта слабее, а укладывается в исходное допущение, оно тем самым
подтверждено консервативным, а не опровергнуто. Второй круг ревью
дополнительно потребовал не прятать холодный прогон (первый настоящий
инференс после установки, без прогретого CUDA/cuDNN JIT-кеша): он дал ≈167 с
на 3,5-мин трек против тёплых ≈24 с — почти в 7 раз медленнее, и это отдельный
факт, который читатель раздела о RunPod Serverless обязан увидеть рядом,
явно отметив, что заложенный коэффициент утилизации 70% на этот разрыв не
рассчитывался (он моделирует простаивающие GPU-секунды между заданиями, а не
задержку конкретного холодного запроса).

- [ ] **Step 8: Написать README**

Создать `api/README.md`:

```markdown
# Karaoke API

Ядро обработки: принимает трек, отделяет минусовку через demucs, отдаёт дорожки.

## Установка

Нужен Python 3.12 — колёса PyTorch под 3.14 могут отсутствовать.

    py -3.12 -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"
    .venv/Scripts/python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
    .venv/Scripts/python -m pip install -e ".[gpu]"

Индекс `cu128` обязателен: RTX 5060 — архитектура Blackwell (sm_120), обычная
сборка PyTorch на ней не запускается. Группа `gpu` из `pyproject.toml`
допускает и уже проверенные версии (`torch 2.11.0+cu128`, `demucs 4.1.0`) —
ставить torch/torchaudio отдельно нужно именно из-за индекса cu128, иначе
встанет несовместимая CPU-сборка.

Проверить GPU настоящим вычислением, а не только `is_available()` (см.
`karaoke_api/gpu.py` — `zeros()` проходит и на несовместимой сборке, потому
что заполнение нулями уходит в `cudaMemsetAsync`, а не в вычислительное ядро):

    .venv/Scripts/python -c "import torch; print((torch.ones(8, device='cuda') * 2).sum().item())"

Ожидается `16.0`. Если падает с `no kernel image` — сборка не та, переустановить под индексом cu128.

## Запуск

    cp .env.example .env
    .venv/Scripts/python -m uvicorn karaoke_api.main:app --reload

Проверка готовности: <http://127.0.0.1:8000/api/health>

## Тесты

    .venv/Scripts/python -m pytest              # быстрые, без GPU
    .venv/Scripts/python -m pytest -m slow -s   # настоящий demucs, с замером

## Разработка без GPU

    KARAOKE_SEPARATOR=fake

Подставляет `FakeSeparator`, копирующий исходник в обе дорожки.
```

- [ ] **Step 9: Коммит**

```bash
cd .. && git add api docs karaoke-context.md
git commit -m "feat(api): локальное разделение через demucs и замер времени"
```

---

## Итог подсистемы A

После задачи 12 работает полная цепочка: трек загружается, ставится в очередь,
разделяется на GPU, дорожки отдаются по HTTP с поддержкой перемотки, файлы
чистятся по TTL. Быстрые тесты идут без GPU, медленный тест печатает фактическое
время обработки.

Следующий план — `2026-08-11-browser-studio.md`, подсистема B.
