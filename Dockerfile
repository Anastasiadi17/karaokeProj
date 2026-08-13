# Один процесс: API плюс собранная студия на том же origin.
#
# ВНИМАНИЕ: этот образ ни разу не собирался — в среде, где он написан, демон
# Docker не запущен. Считайте его черновиком, который надо один раз собрать и
# поправить, а не проверенным артефактом. Что проверять — в конце файла.
#
# Цель — выделенный под или обычная GPU-машина. Для RunPod Serverless нужен
# другой вход (их handler вместо uvicorn), и он пока не спроектирован.

# --- фронтенд -------------------------------------------------------------
FROM node:24-slim AS web

WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Сборка не должна тянуть браузеры Playwright: они нужны только тестам.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm run build

# --- рантайм --------------------------------------------------------------
# cu128 обязателен: RTX 5060 и прочая Blackwell (sm_120) на обычной сборке
# PyTorch не запускается — см. api/README.md.
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# python3.12 — верхняя граница из pyproject (>=3.12,<3.13); ffmpeg нужен
# demucs, libsndfile — soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Слои разделены, чтобы правка кода не пересобирала торч на несколько гигабайт.
COPY api/pyproject.toml api/README.md ./
RUN pip install --index-url https://download.pytorch.org/whl/cu128 \
        torch torchaudio

COPY api/src ./src
RUN pip install -e ".[gpu]"

COPY --from=web /build/dist ./web

ENV KARAOKE_DATA_DIR=/data \
    KARAOKE_DB_PATH=/data/karaoke.db \
    KARAOKE_WEB_DIST=/app/web
VOLUME ["/data"]
EXPOSE 8000

# Модель греется при старте (раннер поднимает разделитель до первой задачи),
# поэтому первые секунды /api/health отвечает model.state=loading — это
# нормально и не повод считать контейнер мёртвым.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "karaoke_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Что проверить при первой сборке:
#   1. Тег базового образа cuda существует и несёт нужный рантайм.
#   2. Колёса cu128 ставятся под python3.12 (индекс их публикует не для всех
#      версий сразу).
#   3. Веса demucs скачиваются в контейнере при прогреве — если сеть закрыта,
#      их надо класть в образ и указывать кеш через переменную окружения.
#   4. `docker run --gpus all` действительно видит карту:
#      /api/health отвечает gpu.available = true.
