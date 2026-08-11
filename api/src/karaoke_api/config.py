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
    allowed_formats: tuple[str, ...] = ("mp3", "wav", "m4a", "flac")


@lru_cache
def get_settings() -> Settings:
    return Settings()
