import sys
import types

import pytest
from fastapi.testclient import TestClient

from karaoke_api import deps, main
from karaoke_api.config import Settings
from karaoke_api.gpu import GpuStatus, check_gpu
from karaoke_api.main import create_app
from karaoke_api.separation.fake import FakeSeparator


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


def test_app_state_passes_gpu_verdict_to_separator(tmp_path, monkeypatch):
    """Вердикт зонда обязан доехать до сборки разделителя.

    До починки сепаратор строился раньше check_gpu() и выбирал устройство
    сам — результат проверки не влиял ни на что.
    """
    seen = {}

    def spy(settings, gpu=None):
        seen["gpu"] = gpu
        return FakeSeparator()

    monkeypatch.setattr(deps, "build_separator", spy)
    status = GpuStatus(False, reason="сломанная сборка", hint="cu128")

    state = deps.AppState.build(
        Settings(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "data" / "db.sqlite",
            separator="fake",
        ),
        status,
    )
    state.store.close()

    assert seen["gpu"] is status


def test_broken_gpu_build_forces_cpu_separator():
    """На сломанной сборке is_available() возвращает True, и DemucsSeparator
    сам взял бы cuda — каждая задача падала бы на no kernel image, пока лог
    обещает CPU. Спека §6 требует продолжать на CPU по-настоящему."""
    pytest.importorskip("torch")
    pytest.importorskip("demucs")

    separator = deps.build_separator(
        Settings(separator="demucs"),
        GpuStatus(False, reason="пробное вычисление дало 3, ожидалось 16"),
    )
    assert separator.device == "cpu"


def test_working_gpu_build_does_not_force_cpu():
    pytest.importorskip("torch")
    pytest.importorskip("demucs")
    import torch

    separator = deps.build_separator(
        Settings(separator="demucs"), GpuStatus(True, device_name="RTX 5060")
    )
    expected = "cuda" if torch.cuda.is_available() else "cpu"
    assert separator.device == expected


def test_unavailable_gpu_at_startup_makes_the_app_use_cpu(tmp_path, monkeypatch):
    """Сквозная проводка: лайфспан → AppState.build → устройство сепаратора."""
    pytest.importorskip("torch")
    pytest.importorskip("demucs")

    monkeypatch.setattr(
        main, "check_gpu",
        lambda: GpuStatus(False, reason="CUDA недоступна", hint="проверьте драйвер"),
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        separator="demucs",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["gpu"]["available"] is False
        assert client.app.state.karaoke.separator.device == "cpu"
