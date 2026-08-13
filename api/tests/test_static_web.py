"""Раздача собранного фронтенда тем же процессом.

Решение «одинаковый источник вместо CORS» до сих пор существовало только на
словах: в разработке origin склеивал прокси Vite, а в продакшене — обратный
прокси, которого никто не написал. Здесь появляется третий вариант, самый
простой: API отдаёт собранный `web/dist` сам, и всё поднимается одной
командой.
"""

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app


def _settings(tmp_path, **kwargs):
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        **kwargs,
    )


@pytest.fixture
def dist(tmp_path):
    """Каталог, похожий на результат `npm run build`."""
    path = tmp_path / "dist"
    (path / "assets").mkdir(parents=True)
    (path / "index.html").write_text(
        "<!doctype html><title>Караоке-студия</title>", encoding="utf-8"
    )
    (path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return path


def test_without_web_dist_nothing_is_served(tmp_path):
    """Значение по умолчанию ничего не меняет: API остаётся API."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_index_is_served_at_root(tmp_path, dist):
    with TestClient(create_app(_settings(tmp_path, web_dist=dist))) as client:
        response = client.get("/")

        assert response.status_code == 200
        assert "Караоке-студия" in response.text


def test_assets_are_served(tmp_path, dist):
    with TestClient(create_app(_settings(tmp_path, web_dist=dist))) as client:
        response = client.get("/assets/app.js")

        assert response.status_code == 200
        assert response.text == "console.log(1)"


def test_api_still_wins_over_static(tmp_path, dist):
    """Порядок маршрутов: статика примонтирована в корень и могла бы съесть
    всё, что зарегистрировано после неё."""
    with TestClient(create_app(_settings(tmp_path, web_dist=dist))) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/jobs/deadbeef").status_code == 404
        assert client.get("/api/jobs/deadbeef").json()["error"] == "not_found"


def test_missing_directory_does_not_break_startup(tmp_path):
    """Путь указан, каталога нет — фронт просто не собран.

    Падать при старте нельзя: API остаётся полезным сам по себе, а сообщение
    «соберите web» человек увидит и без исключения в консоли.
    """
    settings = _settings(tmp_path, web_dist=tmp_path / "nope")

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404


def test_worklet_from_public_is_served(tmp_path, dist):
    """Воркет записи лежит в `public/` и после сборки попадает в корень dist.

    Без него студия не запишет ни одного дубля, а ошибка вылезет только в
    браузере — поэтому проверяется отдельно от прочих файлов.
    """
    (dist / "recorder-worklet.js").write_text(
        "registerProcessor('recorder-processor', class {})", encoding="utf-8"
    )

    with TestClient(create_app(_settings(tmp_path, web_dist=dist))) as client:
        response = client.get("/recorder-worklet.js")

        assert response.status_code == 200
        assert "recorder-processor" in response.text
