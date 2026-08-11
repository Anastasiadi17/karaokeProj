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

    def delete_prefix(self, prefix: str) -> None:
        """Удалить все ключи, начинающиеся с prefix."""
        ...
