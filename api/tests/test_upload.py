import tempfile

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from .conftest import login
from karaoke_api.config import Settings
from karaoke_api.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
        free_monthly_operations=100,
        max_duration_sec=5,
        max_upload_bytes=1_000_000,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        login(c)
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


_BOUNDARY = "----karaokeTestBoundary"
_MULTIPART_TYPE = f"multipart/form-data; boundary={_BOUNDARY}"


def _multipart_body(data: bytes, filename: str = "song.wav") -> bytes:
    head = (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{_BOUNDARY}--\r\n".encode()


def test_rejects_too_large(client, tmp_path, make_wav):
    """Объявленный Content-Length больше лимита — 413 до разбора формы."""
    big = make_wav(name="big.wav", duration_sec=4.0)
    padded = tmp_path / "padded.wav"
    padded.write_bytes(big.read_bytes() + b"\x00" * 1_000_000)
    response = _upload(client, padded)
    assert response.status_code == 413
    assert response.json()["error"] == "too_large"


def test_oversized_body_is_rejected_without_parsing_the_form(client):
    """Отказ обязан наступить ДО разбора multipart, иначе тело уже лежит
    на диске системного temp целиком.

    Тело здесь заведомо неразбираемое: граница объявлена, а содержимого по
    ней нет. Если бы отказ шёл после разбора, ответ был бы про сломанный
    multipart, а не про размер.
    """
    junk = b"x" * (1_000_000 + 8192 + 1)

    response = client.post(
        "/api/tracks",
        headers={"Content-Type": _MULTIPART_TYPE},
        content=junk,
    )

    assert response.status_code == 413
    assert response.json()["error"] == "too_large"


def test_chunked_upload_without_content_length_hits_the_counter(client, tmp_path,
                                                                make_wav):
    """При chunked заголовка Content-Length нет — гейт пропускает запрос,
    и лимит держит счётчик байт в обработчике."""
    big = make_wav(name="big.wav", duration_sec=4.0)
    payload = _multipart_body(big.read_bytes() + b"\x00" * 1_000_000)

    def chunked():
        yield payload

    response = client.post(
        "/api/tracks",
        headers={"Content-Type": _MULTIPART_TYPE},
        content=chunked(),
    )

    assert "content-length" not in {
        k.lower() for k in response.request.headers
    }, "тест бессмыслен, если httpx всё же выставил Content-Length"
    assert response.status_code == 400
    assert response.json()["error"] == "too_large"


def test_chunked_upload_of_valid_track_still_works(client, make_wav):
    """Отсутствие Content-Length не должно ломать нормальную загрузку."""
    payload = _multipart_body(make_wav(duration_sec=1.0).read_bytes())

    def chunked():
        yield payload

    response = client.post(
        "/api/tracks",
        headers={"Content-Type": _MULTIPART_TYPE},
        content=chunked(),
    )

    assert response.status_code == 201
    assert response.json()["track_id"]


def test_extension_does_not_grant_access(client, make_wav, tmp_path):
    """Расширение .wav на мусоре не должно проходить."""
    fake = tmp_path / "liar.wav"
    fake.write_bytes(b"still not audio")
    assert _upload(client, fake).json()["error"] == "unsupported_format"


def test_filename_traversal_is_contained(client, make_wav, tmp_path, monkeypatch):
    """Client-controlled filename must not let the upload write outside the
    staging directory. Staging must use a fixed name, not the client's."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    wav = make_wav(duration_sec=1.0)

    response = _upload(client, wav, name="../../evil.wav")

    assert response.status_code == 201
    # tempfile.TemporaryDirectory() is created directly under tmp_path (our
    # patched tempdir), so "../../evil.wav" relative to it would resolve to
    # tmp_path.parent/evil.wav if the filename were used to build the path.
    assert not (tmp_path.parent / "evil.wav").exists()
    assert not list(tmp_path.parent.glob("**/evil.wav"))


def test_rejects_allowed_but_unlisted_format(client, tmp_path):
    """OGG is readable by soundfile but is not in Settings.allowed_formats,
    so it must be rejected on policy, not on readability."""
    ogg_path = tmp_path / "clip.ogg"
    sf.write(ogg_path, np.zeros(44100, dtype="float32"), 44100, format="OGG")

    response = _upload(client, ogg_path, name="clip.ogg", mime="audio/ogg")

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_format"


@pytest.mark.parametrize("container", ["WAVEX", "RF64"])
def test_accepts_wav_family_containers(client, tmp_path, container):
    """WAVE_FORMAT_EXTENSIBLE и RF64 — обычные .wav, которые штатно выдают
    редакторы и рекордеры. libsndfile читает их без проблем, и до починки
    сервис отвергал их как «формат не поддерживается»: main сверял имя
    контейнера ('wavex', 'rf64') со списком расширений.
    """
    path = tmp_path / f"{container.lower()}.wav"
    sf.write(path, np.zeros((44100, 2), dtype="float32"), 44100,
             format=container)

    response = _upload(client, path, name=f"{container.lower()}.wav")

    assert response.status_code == 201, response.json()
    assert response.json()["track_id"]
