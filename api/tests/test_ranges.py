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
