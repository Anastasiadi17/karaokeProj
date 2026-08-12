import importlib
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CU128_HINT = (
    "Установите сборку PyTorch под CUDA 12.8 или новее: "
    "pip install torch --index-url https://download.pytorch.org/whl/cu128 "
    "(RTX 5060 — архитектура Blackwell, sm_120, обычная сборка её не знает)"
)


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    device_name: str | None = None
    reason: str | None = None
    hint: str | None = None


def check_gpu() -> GpuStatus:
    """Проверить GPU настоящим тензором, а не только is_available().

    is_available() возвращает True и на несовместимой сборке — ошибка
    вылезает лишь при первом вычислении. Ловим её здесь, при старте,
    а не на первой задаче пользователя.
    """
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return GpuStatus(False, reason=f"torch не импортируется: {exc}",
                         hint=CU128_HINT)

    if torch is None:
        return GpuStatus(False, reason="torch не установлен", hint=CU128_HINT)

    if not torch.cuda.is_available():
        return GpuStatus(False, reason="CUDA недоступна", hint=CU128_HINT)

    try:
        torch.zeros(8, device="cuda")
    except Exception as exc:
        return GpuStatus(False, reason=f"пробное вычисление упало: {exc}",
                         hint=CU128_HINT)

    return GpuStatus(True, device_name=torch.cuda.get_device_name(0))
