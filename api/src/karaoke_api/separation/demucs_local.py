import logging
import threading
from pathlib import Path

import soundfile as sf
import torch
import torchaudio
from demucs.apply import apply_model
from demucs.audio import save_audio
from demucs.pretrained import get_model

from .base import ProgressCallback, SeparationResult

log = logging.getLogger(__name__)

# Длительность пробного тензора для warmup(). По замеру двумя точками
# (karaoke-context.md 4.5) счёт стоит ≈0,03 с на секунду звука, то есть пять
# секунд — это ≈0,15 с. При этом пять секунд попадают в обычную сегментную
# ветку apply_model, а не в вырожденную, то есть прогревают те же ядра, что
# и настоящая задача.
WARMUP_SECONDS = 5.0


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
        self._model_lock = threading.Lock()

    def _ensure_model(self):
        """Загрузить модель один раз. Повторные вызовы отдают ту же.

        Замок здесь страхует будущее, а не чинит настоящее. Инвариант такой:
        модели касается только рабочий поток раннера — прогрев идёт в нём же,
        первым делом в run_forever. Инвариант нигде больше не записан, а без
        замка любой второй вызывающий (фоновый поток, второй раннер) получил
        бы две параллельные загрузки: лишние секунды, лишняя видеопамять и
        ни одного сообщения о том, что что-то не так.

        Схема double-checked: быстрая проверка без замка на горячем пути,
        повторная — под ним, потому что между первой проверкой и захватом
        замка модель мог загрузить кто-то другой.
        """
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                log.info("загружаю модель %s на %s", self._model_name,
                         self.device)
                self._model = get_model(self._model_name).to(
                    self.device).eval()
        return self._model

    def warmup(self) -> None:
        """Загрузить модель и прогнать через неё короткую тишину.

        Издержки первого запроса — две разные величины: загрузка весов из
        локального кеша (≈2,6 с, наступает на первом _ensure_model в
        процессе) и JIT-компиляция ядер CUDA (≈20 с, наступает на первом
        настоящем apply_model на машине или в контейнере). Прогрев, который
        только грузит веса, снял бы с первого пользователя меньшую из двух.

        Прогон идёт через _apply_with_fallback — тот же путь, которым идут
        настоящие задачи. Прогрев через другой путь прогрел бы не те ядра.

        На CPU пробного инференса нет: компилировать нечего, создание
        примитивов oneDNN стоит миллисекунды, а прогон пяти секунд через
        htdemucs на процессоре — десятки секунд. Тратить их, чтобы не
        сэкономить ничего, незачем.

        Пометка о деградации наружу не идёт: warmup не создаёт
        SeparationResult. Откат на CPU на пяти секундах тишины означает, что
        что-то серьёзно не так, поэтому он логируется предупреждением.
        """
        model = self._ensure_model()
        if self.device != "cuda":
            return
        frames = int(WARMUP_SECONDS * model.samplerate)
        _, degraded = self._apply_with_fallback(model, torch.zeros(2, frames))
        if degraded:
            log.warning(
                "прогрев прошёл только на CPU: видеопамяти не хватило даже "
                "на %.0f с тишины", WARMUP_SECONDS,
            )

    def _apply_with_fallback(
        self, model, wav: torch.Tensor
    ) -> tuple[torch.Tensor, bool]:
        """Прогнать модель, на нехватке видеопамяти — откатиться на CPU.

        Откат осмыслен только если основной проход шёл на CUDA: на "cpu"
        откатываться уже некуда, а OutOfMemoryError там взяться неоткуда —
        поэтому при self.device != "cuda" ветку отката пропускаем явно,
        а не полагаемся на то, что исключение просто не наступит.

        Восстановление устройства модели — в finally: если сорвётся и сам
        CPU-проход, self._model не должен навсегда осесть на CPU, пока
        self.device продолжает утверждать "cuda".

        Возвращает (sources, degraded) — второй элемент True, только если
        откат на CPU действительно произошёл и досчитал. Наверх это
        сигнализирует separate(), которому нужно положить пометку о
        деградации в SeparationResult — иначе о том, что задача выполнена
        медленным путём, никто не узнает.
        """
        if self.device != "cuda":
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0], False
        try:
            with torch.no_grad():
                return apply_model(model, wav[None], device=self.device,
                                   progress=False)[0], False
        except torch.cuda.OutOfMemoryError as exc:
            log.warning("нехватка видеопамяти, повторяю на CPU: %s", exc)
            torch.cuda.empty_cache()
            model.to("cpu")
            try:
                with torch.no_grad():
                    return apply_model(model, wav[None], device="cpu",
                                       progress=False)[0], True
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
        sources, degraded = self._apply_with_fallback(model, wav)

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

        return SeparationResult(vocals=vocals_path, no_vocals=no_vocals_path,
                                degraded=degraded)
