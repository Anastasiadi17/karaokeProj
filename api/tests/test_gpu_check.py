import sys
import types

import pytest
from fastapi.testclient import TestClient

from karaoke_api.config import Settings
from karaoke_api.gpu import GpuStatus, check_gpu
from karaoke_api.main import create_app


def _fake_torch(*, cuda_available: bool, raises: Exception | None = None,
                capability=(12, 0), name="RTX 5060"):
    module = types.ModuleType("torch")

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return cuda_available

        @staticmethod
        def get_device_name(idx: int = 0) -> str:
            return name

        @staticmethod
        def get_device_capability(idx: int = 0):
            return capability

    def _zeros(_n, device=None):
        if raises is not None:
            raise raises
        return object()

    module.cuda = _Cuda
    module.zeros = _zeros
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
