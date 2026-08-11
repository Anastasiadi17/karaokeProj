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
