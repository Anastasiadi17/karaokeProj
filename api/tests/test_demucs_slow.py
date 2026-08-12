import time

import numpy as np
import pytest
import soundfile as sf

# Импорты уровня модуля выполняются на этапе СБОРКИ, а `-m 'not slow'`
# фильтрует уже после неё. Без этих двух строк `pytest -q` на машине без
# gpu-группы падал бы ошибкой сбора — вопреки ограничению плана «быстрые
# тесты не требуют GPU».
#
# exc_type=ImportError, а не умолчание ModuleNotFoundError: на Windows
# торч бывает не столько отсутствующим, сколько сломанным (не подхватились
# DLL CUDA), и тогда импорт даёт ImportError при найденном пакете. Быстрый
# набор не должен разваливаться и в этом случае.
torch = pytest.importorskip("torch", exc_type=ImportError)
pytest.importorskip("demucs", exc_type=ImportError)

from karaoke_api.audio.probe import probe_audio  # noqa: E402
from karaoke_api.separation import demucs_local  # noqa: E402
from karaoke_api.separation.demucs_local import (  # noqa: E402
    DemucsSeparator,
    _load_wav,
)

# Маркер slow — только на тестах, которым нужны настоящая модель и GPU.
# Порядок каналов и оба теста отката бьют по структурным свойствам кода
# (calls == ["cpu", "cuda"]), модели не требуют, и именно этот класс
# регрессий быстрый набор обязан ловить.

# Относительная ошибка реконструкции (RMS разности к RMS исходного микса),
# измеренная на настоящих прогонах этого теста на RTX 5060: 0.0097 и 0.0060
# (два прогона подряд, синус 440 Гц 30 с). htdemucs — не маскирующая модель,
# точной суммы в 4 стема не даёт, так что нулевой ошибки не ждём. Порог —
# 0.05, это ~5-8x запас от обоих замеров: настоящая поломка (потерянная
# транспозиция, забытая денормировка, обнулённый канал) сдвигает ошибку на
# порядки, а не на десятки процентов, так что запас не грозит ложными
# срабатываниями на нормальном шуме модели.
MAX_RECONSTRUCTION_RELATIVE_ERROR = 0.05

# Модель времени обработки, построенная по двум длительностям на RTX 5060
# (30 с -> 3,5 с и 600 с -> 20,9 с; второй замер разовый, скриптом, в набор не
# добавлялся). Одной точки не хватает принципиально: постоянная составляющая —
# загрузка весов из локального кеша и перенос на GPU — в линейной
# экстраполяции умножается вместе с переменной и завышает трёхминутный трек
# втрое. Числа машинно-зависимые и используются только в печати замера, ни
# одно утверждение теста на них не опирается. Разбор — karaoke-context.md 4.5.
STARTUP_OVERHEAD_SEC = 2.6
SEC_PER_AUDIO_SEC = 0.0305


def _write_asymmetric_stereo(path, duration_sec: float = 0.2,
                             sample_rate: int = 44100):
    """WAV с разным сигналом в левом и правом канале.

    conftest.make_wav пишет одинаковый синус в оба канала — на нём
    перепутанный порядок осей после транспозиции (.T) в _load_wav остался
    бы незамеченным. Здесь каналы намеренно разные постоянные значения.
    """
    frames = int(duration_sec * sample_rate)
    left = np.full(frames, 0.5, dtype=np.float32)
    right = np.full(frames, -0.5, dtype=np.float32)
    data = np.stack([left, right], axis=1)
    sf.write(str(path), data, sample_rate)
    return path


def test_load_wav_preserves_channel_order(tmp_path):
    """_load_wav не путает левый и правый канал транспозицией."""
    source = _write_asymmetric_stereo(tmp_path / "lr.wav")
    wav, sample_rate = _load_wav(source)

    assert sample_rate == 44100
    assert wav.shape[0] == 2
    assert wav[0].mean().item() == pytest.approx(0.5, abs=1e-3)
    assert wav[1].mean().item() == pytest.approx(-0.5, abs=1e-3)


class _FakeModel:
    """Минимальная замена demucs-модели: помнит, на какие устройства её
    переносили. Настоящий OOM на GPU не воспроизвести детерминированно, а
    подменять apply_model, чтобы протестировать само разделение, было бы
    обманом — тест на моках ничего не доказал бы про качество звука.
    Восстановление устройства в finally — чисто структурное свойство
    _apply_with_fallback, и его честно проверить без единого обращения к
    настоящему GPU.
    """

    def __init__(self):
        self.calls: list[str] = []
        # Нужны только для тестов, гоняющих separate() целиком поверх этой
        # подделки (пропуская _ensure_model): демucs ждёт от модели ровно
        # эти два атрибута для сборки стемов и записи файлов.
        self.sources = ["drums", "bass", "other", "vocals"]
        self.samplerate = 44100

    def to(self, device):
        self.calls.append(device)
        return self


def test_oom_fallback_restores_device_even_if_cpu_pass_also_fails(monkeypatch):
    """Если сорвался и сам CPU-проход, модель не должна навсегда осесть на
    CPU, пока self.device продолжает утверждать "cuda"."""
    separator = DemucsSeparator(device="cuda")
    fake_model = _FakeModel()

    def fake_apply_model(model, mix, device, progress=False):
        if device == "cuda":
            raise torch.cuda.OutOfMemoryError("симулированная нехватка VRAM")
        raise RuntimeError("CPU-проход тоже не справился (симуляция)")

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    with pytest.raises(RuntimeError):
        separator._apply_with_fallback(fake_model, torch.zeros(2, 10))

    assert fake_model.calls == ["cpu", "cuda"]


def test_oom_fallback_skipped_when_device_is_cpu(monkeypatch):
    """При self.device == "cpu" откатываться некуда — ветка не должна
    пытаться переносить модель между устройствами вовсе."""
    separator = DemucsSeparator(device="cpu")
    fake_model = _FakeModel()

    def fake_apply_model(model, mix, device, progress=False):
        raise torch.cuda.OutOfMemoryError("не должно ловиться на cpu-пути")

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    with pytest.raises(torch.cuda.OutOfMemoryError):
        separator._apply_with_fallback(fake_model, torch.zeros(2, 10))

    assert fake_model.calls == []


def test_apply_with_fallback_reports_degraded_true_after_successful_retry(
    monkeypatch,
):
    """Спека требует пометку о деградации, когда откат на CPU реально
    произошёл и досчитал — иначе подсистема B не сможет показать
    пользователю, что трек обработан медленным путём."""
    separator = DemucsSeparator(device="cuda")
    fake_model = _FakeModel()

    def fake_apply_model(model, mix, device, progress=False):
        if device == "cuda":
            raise torch.cuda.OutOfMemoryError("симулированная нехватка VRAM")
        # apply_model возвращает батч, из которого _apply_with_fallback сам
        # берёт [0] — поэтому здесь на одну размерность больше, чем в
        # ожидаемых sources.
        return torch.zeros(1, 4, *mix.shape[1:])

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    sources, degraded = separator._apply_with_fallback(
        fake_model, torch.zeros(2, 10)
    )

    assert degraded is True
    assert fake_model.calls == ["cpu", "cuda"]
    assert sources.shape == (4, 2, 10)


def test_apply_with_fallback_reports_degraded_false_without_oom(monkeypatch):
    """Без отката пометки о деградации быть не должно — иначе нормально
    обработанный на GPU трек тоже показался бы деградировавшим."""
    separator = DemucsSeparator(device="cuda")
    fake_model = _FakeModel()

    def fake_apply_model(model, mix, device, progress=False):
        return torch.zeros(1, 4, *mix.shape[1:])

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    sources, degraded = separator._apply_with_fallback(
        fake_model, torch.zeros(2, 10)
    )

    assert degraded is False
    assert fake_model.calls == []


def test_separate_result_degraded_true_reaches_job_result(
    monkeypatch, make_wav, tmp_path
):
    """Проверяет весь путь separate(), а не только _apply_with_fallback:
    пометка о деградации обязана доехать до SeparationResult, который
    JobRunner кладёт в результат задачи как есть."""
    separator = DemucsSeparator(device="cuda")
    fake_model = _FakeModel()
    monkeypatch.setattr(separator, "_ensure_model", lambda: fake_model)

    def fake_apply_model(model, mix, device, progress=False):
        if device == "cuda":
            raise torch.cuda.OutOfMemoryError("симулированная нехватка VRAM")
        return torch.zeros(1, 4, *mix.shape[1:])

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    source = make_wav(duration_sec=0.2, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()

    result = separator.separate(source, out, lambda stage, pct: None)

    assert result.degraded is True
    assert result.vocals.is_file()
    assert result.no_vocals.is_file()


def test_separate_result_not_degraded_without_fallback(
    monkeypatch, make_wav, tmp_path
):
    separator = DemucsSeparator(device="cuda")
    fake_model = _FakeModel()
    monkeypatch.setattr(separator, "_ensure_model", lambda: fake_model)

    def fake_apply_model(model, mix, device, progress=False):
        return torch.zeros(1, 4, *mix.shape[1:])

    monkeypatch.setattr(demucs_local, "apply_model", fake_apply_model)

    source = make_wav(duration_sec=0.2, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()

    result = separator.separate(source, out, lambda stage, pct: None)

    assert result.degraded is False


@pytest.mark.slow
def test_separates_real_audio_and_reports_timing(make_wav, tmp_path, capsys):
    """Проверяет интеграцию и печатает фактическое время обработки.

    Это число закрывает допущение «25 секунд» из раздела 4.5 контекстного
    документа — главную дыру в юнит-экономике. Сам по себе один клип на это
    не отвечает: пересчитывать его на трек нужно двухточечной моделью
    (константы выше), а не пропорцией.
    """
    duration = 30.0
    source = make_wav(duration_sec=duration, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()

    separator = DemucsSeparator()
    assert separator.device == "cuda", (
        "тест требует настоящий GPU (маркер slow) — на CPU замер "
        "бессмысленно сравнивать с допущением о серверном GPU-инференсе"
    )

    started = time.perf_counter()
    result = separator.separate(source, out, lambda stage, pct: None)
    elapsed = time.perf_counter() - started

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()

    vocals_info = probe_audio(result.vocals)
    no_vocals_info = probe_audio(result.no_vocals)
    assert abs(no_vocals_info.duration_sec - duration) < 0.5
    assert no_vocals_info.channels == 2
    assert vocals_info.sample_rate == 44100
    assert no_vocals_info.sample_rate == 44100

    source_wav, _ = sf.read(str(source), dtype="float32", always_2d=True)
    vocals_wav, _ = sf.read(str(result.vocals), dtype="float32", always_2d=True)
    no_vocals_wav, _ = sf.read(str(result.no_vocals), dtype="float32",
                               always_2d=True)

    # Дорожки не должны быть побитово одинаковыми — иначе разделение
    # фактически не произошло (например, обе записаны из одного тензора).
    assert not np.array_equal(vocals_wav, no_vocals_wav)

    # Инвариант реконструкции: vocals + no_vocals — это сумма всех четырёх
    # источников htdemucs и должна приближённо восстанавливать исходный
    # микс. Проверка разом бьёт по денормировке, транспозиции и потере
    # канала — любая из этих поломок сдвинет ошибку далеко за порог.
    frames = min(len(source_wav), len(vocals_wav), len(no_vocals_wav))
    recon = vocals_wav[:frames] + no_vocals_wav[:frames]
    mix = source_wav[:frames]
    diff_rms = float(np.sqrt(np.mean((recon - mix) ** 2)))
    mix_rms = float(np.sqrt(np.mean(mix ** 2)))
    relative_error = diff_rms / mix_rms

    ratio = elapsed / duration
    modelled = STARTUP_OVERHEAD_SEC + SEC_PER_AUDIO_SEC * 210
    with capsys.disabled():
        print(
            f"\n=== ЗАМЕР ===\n"
            f"устройство:        {separator.device}\n"
            f"длительность:      {duration:.1f} с\n"
            f"обработка заняла:  {elapsed:.1f} с\n"
            f"на 3,5-мин трек:   {modelled:.1f} с "
            f"({STARTUP_OVERHEAD_SEC} с постоянных + "
            f"{SEC_PER_AUDIO_SEC} с/с звука, по двум точкам)\n"
            f"  для сравнения:   {ratio * 210:.1f} с — линейная экстраполяция\n"
            f"                   ОТ ЭТОГО КЛИПА, завышает втрое (см. 4.5)\n"
            f"ошибка реконстр.:  {relative_error:.4f} "
            f"(порог {MAX_RECONSTRUCTION_RELATIVE_ERROR})\n"
            f"=============="
        )

    assert relative_error < MAX_RECONSTRUCTION_RELATIVE_ERROR
