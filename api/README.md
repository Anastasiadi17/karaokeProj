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
