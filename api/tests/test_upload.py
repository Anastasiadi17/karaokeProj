import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        max_duration_sec=5,
        max_upload_bytes=1_000_000,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _upload(client, path, name="song.wav", mime="audio/wav"):
    with open(path, "rb") as fh:
        return client.post("/api/tracks", files={"file": (name, fh, mime)})


def test_accepts_valid_wav(client, make_wav):
    response = _upload(client, make_wav(duration_sec=1.0))
    assert response.status_code == 201
    body = response.json()
    assert body["track_id"]
    assert body["job_id"]


def test_rejects_too_long(client, make_wav):
    # 5.2s at the fixture's default 44.1kHz/stereo/16-bit stays under the
    # 1_000_000 byte limit (~917KB) while still exceeding max_duration_sec=5,
    # so this exercises the duration check specifically, not the size check.
    response = _upload(client, make_wav(duration_sec=5.2))
    assert response.status_code == 400
    assert response.json()["error"] == "too_long"


def test_rejects_non_audio(client, tmp_path):
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all")
    response = _upload(client, junk)
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_format"


def test_rejects_too_large(client, tmp_path, make_wav):
    big = make_wav(name="big.wav", duration_sec=4.0)
    padded = tmp_path / "padded.wav"
    padded.write_bytes(big.read_bytes() + b"\x00" * 1_000_000)
    response = _upload(client, padded)
    assert response.status_code == 400
    assert response.json()["error"] == "too_large"


def test_extension_does_not_grant_access(client, make_wav, tmp_path):
    """Расширение .wav на мусоре не должно проходить."""
    fake = tmp_path / "liar.wav"
    fake.write_bytes(b"still not audio")
    assert _upload(client, fake).json()["error"] == "unsupported_format"
