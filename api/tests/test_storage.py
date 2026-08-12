from pathlib import Path

import pytest

from karaoke_api.storage import local
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
    """Keys that normalize to the storage root itself are rejected."""
    with pytest.raises(ValueError):
        storage.store_file("tracks/..", source_file)


def test_key_with_interior_traversal_to_root_is_rejected(storage, source_file):
    """Keys with interior .. segments that escape to root are rejected."""
    with pytest.raises(ValueError):
        storage.store_file("a/b/../..", source_file)


def test_delete_prefix_nonexistent_is_noop(storage):
    """Deleting a non-existent prefix should not raise an error."""
    # This should not raise; it's a no-op
    storage.delete_prefix("nonexistent/path")


def test_key_is_invisible_until_file_is_fully_written(storage, source_file,
                                                      monkeypatch):
    """Ключ появляется целиком или не появляется.

    Копирование прямо в целевой путь означало бы, что HTTP-запрос, пришедший
    в момент записи дорожки, отдаст частично записанный WAV с кодом 200 и
    Content-Length, снятым в гонке. FakeSeparator пишет за микросекунды, так
    что окно ловится только изнутри самого копирования.
    """
    real_copyfile = local.shutil.copyfile
    seen = {}

    def spying_copyfile(src, dst):
        result = real_copyfile(src, dst)
        # Момент, когда байты уже на диске, но постановка ещё не завершена.
        seen["visible_mid_write"] = storage.exists("tracks/abc/stem.wav")
        return result

    monkeypatch.setattr(local.shutil, "copyfile", spying_copyfile)
    storage.store_file("tracks/abc/stem.wav", source_file)

    assert seen["visible_mid_write"] is False
    assert storage.exists("tracks/abc/stem.wav")
    assert storage.size("tracks/abc/stem.wav") == 10


def test_failed_copy_leaves_neither_key_nor_temp_file(storage, source_file,
                                                      monkeypatch, tmp_path):
    """Сорвавшаяся запись не должна оставлять ни полуфабриката под ключом,
    ни мусорного временного файла рядом."""
    def failing_copyfile(src, dst):
        Path(dst).write_bytes(b"01234")  # половина исходных байт
        raise OSError("диск кончился (симуляция)")

    monkeypatch.setattr(local.shutil, "copyfile", failing_copyfile)

    with pytest.raises(OSError):
        storage.store_file("tracks/abc/stem.wav", source_file)

    assert not storage.exists("tracks/abc/stem.wav")
    leftovers = list((tmp_path / "store" / "tracks" / "abc").iterdir())
    assert leftovers == []
