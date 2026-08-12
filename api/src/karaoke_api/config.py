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
    # m4a исключён: libsndfile (через soundfile) его не читает —
    # sf.available_formats() отдаёт MP3, но не M4A/AAC/MP4. Обещать формат,
    # который тут же отвергается как нечитаемый, хуже, чем не обещать вовсе.
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
