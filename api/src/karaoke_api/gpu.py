import importlib
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CU128_HINT = (
    "Установите сборку PyTorch под CUDA 12.8 или новее: "
    "pip install torch --index-url https://download.pytorch.org/whl/cu128 "
    "(RTX 5060 — архитектура Blackwell, sm_120, обычная сборка её не знает)"
)

NO_GPU_HINT = (
    "Проверьте, что видеокарта NVIDIA присутствует и установлен драйвер "
    "(nvidia-smi должен показывать GPU) — переустановка под cu128 здесь "
    "не поможет"
)


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    device_name: str | None = None
    reason: str | None = None
    hint: str | None = None


def check_gpu() -> GpuStatus:
    """Проверить GPU настоящим вычислением, а не только is_available().

    is_available() возвращает True и на несовместимой сборке — ошибка
    вылезает лишь при первом вычислении. zeros() тоже не годится: заполнение
    нулями свежего тензора реализовано через cudaMemsetAsync — операцию
    уровня драйвера, которая пройдёт и без ядер под нашу архитектуру. Нужна
    настоящая арифметика, а .item() ещё и синхронизирует: без него
    асинхронная ошибка запуска ядра может не всплыть на месте вызова.

    Всё тело после импорта обёрнуто в try — сломанный драйвер способен
    уронить даже is_available(), а по контракту задачи проверка не должна
    валить старт приложения ни при каких обстоятельствах.
    """
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return GpuStatus(False, reason=f"torch не импортируется: {exc}",
                         hint=CU128_HINT)

    try:
        if not torch.cuda.is_available():
            return GpuStatus(False, reason="CUDA недоступна", hint=NO_GPU_HINT)

        probe = (torch.ones(8, device="cuda") * 2).sum().item()
        if probe != 16:
            return GpuStatus(
                False,
                reason=f"пробное вычисление дало {probe}, ожидалось 16",
                hint=CU128_HINT,
            )

        try:
            device_name = torch.cuda.get_device_name(0)
        except Exception:
            # Само вычисление сработало — GPU реально доступен. Не проваливать
            # весь проб из-за того, что не удалось узнать только имя устройства.
            device_name = None

        return GpuStatus(True, device_name=device_name)
    except Exception as exc:
        return GpuStatus(False, reason=f"проверка GPU упала: {exc}",
                         hint=CU128_HINT)
