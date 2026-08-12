import logging
from pathlib import Path

import soundfile as sf
import torch
import torchaudio
from demucs.apply import apply_model
from demucs.audio import save_audio
from demucs.pretrained import get_model

from .base import ProgressCallback, SeparationResult

log = logging.getLogger(__name__)


def _load_wav(source: Path) -> tuple[torch.Tensor, int]:
    """Прочитать аудио через soundfile, вернуть тензор (channels, frames).

    torchaudio.load в 2.11 делегирует в load_with_torchcodec и требует пакет
    torchcodec, которого нет и не будет — падает с ImportError при первом
    вызове. soundfile уже прямая зависимость проекта (audio/probe.py читает
    им же). soundfile отдаёт (frames, channels) при always_2d=True — нужна
    транспозиция и dtype=float32, которых ждут demucs и остальной код.
    """
    data, sample_rate = sf.read(str(source), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T).contiguous()
    return wav, sample_rate


class DemucsSeparator:
    """Локальное разделение через demucs. Модель грузится один раз.

    Работает в режиме двух дорожек: вокал и всё остальное. Внутри модель
    считает четыре источника и складывает три из них в аккомпанемент.
    """

    def __init__(self, model_name: str = "htdemucs",
                 device: str | None = None) -> None:
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            log.info("загружаю модель %s на %s", self._model_name, self.device)
            self._model = get_model(self._model_name).to(self.device).eval()
        return self._model

    def _apply_with_fallback(self, model, wav: torch.Tensor) -> torch.Tensor:
        """Прогнать модель, на нехватке видеопамяти — откатиться на CPU.

        Откат осмыслен только если основной проход шёл на CUDA: на "cpu"
        откатываться уже некуда, а OutOfMemoryError там взяться неоткуда —
        поэтому при self.device != "cuda" ветку отката пропускаем явно,
        а не полагаемся на то, что исключение просто не наступит.

        Восстановление устройства модели — в finally: если сорвётся и сам
        CPU-проход, self._model не должен навсегда осесть на CPU, пока
        self.device продолжает утверждать "cuda".
        """
        if self.device != "cuda":
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0]
        try:
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0]
        except torch.cuda.OutOfMemoryError as exc:
            log.warning("нехватка видеопамяти, повторяю на CPU: %s", exc)
            torch.cuda.empty_cache()
            model.to("cpu")
            try:
                with torch.no_grad():
                    return apply_model(model, wav[None], device="cpu",
                                       progress=False)[0]
            finally:
                model.to(self.device)

    def separate(self, source: Path, out_dir: Path,
                 on_progress: ProgressCallback) -> SeparationResult:
        on_progress("loading", 0.0)
        model = self._ensure_model()

        wav, sample_rate = _load_wav(source)
        if wav.shape[0] > 2:
            # Больше двух каналов (например, 5.1 в flac) — demucs/apply_model
            # ждёт ровно audio_channels (2 для htdemucs) и упадёт по форме
            # тензора. Сводим в моно и дублируем, как и одноканальный вход.
            wav = wav.mean(0, keepdim=True)
        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        if sample_rate != model.samplerate:
            wav = torchaudio.functional.resample(wav, sample_rate,
                                                 model.samplerate)

        reference = wav.mean(0)
        wav = (wav - reference.mean()) / (reference.std() + 1e-8)

        on_progress("separating", 0.1)
        sources = self._apply_with_fallback(model, wav)

        sources = sources * (reference.std() + 1e-8) + reference.mean()
        stems = dict(zip(model.sources, sources))

        vocals_path = Path(out_dir) / "vocals.wav"
        no_vocals_path = Path(out_dir) / "no_vocals.wav"

        vocals = stems["vocals"]
        no_vocals = sum(t for name, t in stems.items() if name != "vocals")

        # Инференс уже завершён, тензоры дорожек посчитаны — дальше только
        # запись на диск. Сигнализируем "writing" здесь, а не до сборки
        # стемов (как в исходном черновике): тогда прогресс-бар был бы
        # нечестным — показывал бы запись, пока ещё идёт вычисление
        # аккомпанемента (сумма трёх тензоров). FakeSeparator несёт тот же
        # баг (Task 4), там он безвреден, потому что запись — это простое
        # копирование файла, а не отдельная тяжёлая стадия.
        on_progress("writing", 0.9)
        save_audio(vocals, str(vocals_path), model.samplerate)
        save_audio(no_vocals, str(no_vocals_path), model.samplerate)

        return SeparationResult(vocals=vocals_path, no_vocals=no_vocals_path)
