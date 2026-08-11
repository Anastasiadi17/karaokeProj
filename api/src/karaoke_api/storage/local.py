import shutil
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
        if root not in path.parents:
            raise ValueError(f"ключ выходит за пределы хранилища: {key!r}")
        return path

    def store_file(self, key: str, src: Path) -> None:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

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

    def delete_prefix(self, prefix: str) -> None:
        target = self._resolve(prefix)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
