import time

import pytest

from karaoke_api.audio.probe import probe_audio
from karaoke_api.separation.demucs_local import DemucsSeparator

pytestmark = pytest.mark.slow


def test_separates_real_audio_and_reports_timing(make_wav, tmp_path, capsys):
    """Проверяет интеграцию и печатает фактическое время обработки.

    Это число закрывает допущение «25 секунд» из раздела 4.5
    контекстного документа — главную дыру в юнит-экономике.
    """
    duration = 30.0
    source = make_wav(duration_sec=duration, sample_rate=44100, channels=2)
    out = tmp_path / "out"
    out.mkdir()

    separator = DemucsSeparator()
    started = time.perf_counter()
    result = separator.separate(source, out, lambda stage, pct: None)
    elapsed = time.perf_counter() - started

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()

    info = probe_audio(result.no_vocals)
    assert abs(info.duration_sec - duration) < 0.5
    assert info.channels == 2

    ratio = elapsed / duration
    with capsys.disabled():
        print(
            f"\n=== ЗАМЕР ===\n"
            f"устройство:        {separator.device}\n"
            f"длительность:      {duration:.1f} с\n"
            f"обработка заняла:  {elapsed:.1f} с\n"
            f"на 3,5-мин трек:   {ratio * 210:.1f} с (экстраполяция)\n"
            f"=============="
        )
