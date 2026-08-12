import sys
import types

from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.gpu import GpuStatus, check_gpu
from karaoke_api.main import create_app


class _FakeTensor:
    """Достаточно арифметики, чтобы (ones(8) * 2).sum().item() дало 16."""

    def __init__(self, value):
        self._value = value

    def __mul__(self, other):
        return _FakeTensor(self._value * other)

    def sum(self):
        return self

    def item(self):
        return self._value


def _fake_torch(*, cuda_available: bool, raises: Exception | None = None,
                capability=(12, 0), name="RTX 5060", wrong_compute=False,
                is_available_raises: Exception | None = None,
                name_raises: Exception | None = None):
    module = types.ModuleType("torch")

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            if is_available_raises is not None:
                raise is_available_raises
            return cuda_available

        @staticmethod
        def get_device_name(idx: int = 0) -> str:
            if name_raises is not None:
                raise name_raises
            return name

        @staticmethod
        def get_device_capability(idx: int = 0):
            return capability

    def _ones(n, device=None):
        if raises is not None:
            raise raises
        # ones(8) содержит 8 единиц, поэтому их сумма равна n; после *2 в
        # check_gpu() это даёт 16 на «правильной» сборке.
        value = 3 if wrong_compute else n
        return _FakeTensor(value)

    module.cuda = _Cuda
    module.ones = _ones
    return module


def test_missing_torch_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    status = check_gpu()
    assert isinstance(status, GpuStatus)
    assert status.available is False
    assert "torch" in status.reason.lower()


def test_no_cuda_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=False))
    status = check_gpu()
    assert status.available is False
    assert "cuda" in status.reason.lower()


def test_kernel_image_failure_gives_cu128_hint(monkeypatch):
    err = RuntimeError("CUDA error: no kernel image is available for execution")
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True, raises=err))
    status = check_gpu()
    assert status.available is False
    assert "cu128" in status.hint


def test_wrong_compute_result_is_reported(monkeypatch):
    """Ядро запустилось без ошибки, но посчитало неверно — тоже не available."""
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True, wrong_compute=True))
    status = check_gpu()
    assert status.available is False
    assert "16" in status.reason
    assert "cu128" in status.hint


def test_is_available_raising_does_not_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True,
                                    is_available_raises=RuntimeError("driver fault")))
    status = check_gpu()
    assert isinstance(status, GpuStatus)
    assert status.available is False
    assert status.hint is not None


def test_device_name_failure_still_reports_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True,
                                    name_raises=RuntimeError("no name")))
    status = check_gpu()
    assert status.available is True
    assert status.device_name is None


def test_working_gpu_is_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch",
                        _fake_torch(cuda_available=True))
    status = check_gpu()
    assert status.available is True
    assert status.device_name == "RTX 5060"
    assert status.reason is None


def test_health_endpoint_exposes_gpu(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="fake",
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/health").json()
        assert "gpu" in body
        assert "available" in body["gpu"]
        assert body["separator"] == "fake"
