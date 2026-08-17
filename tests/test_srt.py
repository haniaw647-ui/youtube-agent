from src.workers.stages._srt import build_srt, format_srt_timestamp


def test_format_srt_timestamp_basic():
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(1.5) == "00:00:01,500"
    assert format_srt_timestamp(65) == "00:01:05,000"
    assert format_srt_timestamp(3661.25) == "01:01:01,250"


def test_build_srt_cumulative_timing():
    segment_durations = [{"scene": 1, "duration": 3.0}, {"scene": 2, "duration": 2.0}]
    segment_by_scene = {1: {"narration": "First line"}, 2: {"narration": "Second line"}}

    srt = build_srt(segment_durations, segment_by_scene)

    assert "1\n00:00:00,000 --> 00:00:03,000\nFirst line" in srt
    assert "2\n00:00:03,000 --> 00:00:05,000\nSecond line" in srt


def test_build_srt_handles_missing_narration():
    segment_durations = [{"scene": 1, "duration": 1.0}]
    srt = build_srt(segment_durations, {})
    assert "00:00:00,000 --> 00:00:01,000" in srt
