from karaoke_api.separation.fake import FakeSeparator


def test_produces_two_stems(make_wav, tmp_path):
    source = make_wav(duration_sec=1.0)
    out = tmp_path / "out"
    out.mkdir()

    result = FakeSeparator().separate(source, out, lambda stage, pct: None)

    assert result.vocals.is_file()
    assert result.no_vocals.is_file()
    assert result.vocals.name == "vocals.wav"
    assert result.no_vocals.name == "no_vocals.wav"


def test_reports_stages_in_order(make_wav, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    seen: list[str] = []

    FakeSeparator().separate(
        make_wav(), out, lambda stage, pct: seen.append(stage)
    )

    assert seen == ["loading", "separating", "writing"]


def test_stems_are_readable_audio(make_wav, tmp_path):
    from karaoke_api.audio.probe import probe_audio

    out = tmp_path / "out"
    out.mkdir()
    result = FakeSeparator().separate(make_wav(duration_sec=1.5), out,
                                      lambda s, p: None)

    info = probe_audio(result.no_vocals)
    assert abs(info.duration_sec - 1.5) < 0.05
