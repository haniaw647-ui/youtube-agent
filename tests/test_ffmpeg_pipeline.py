"""Real ffmpeg integration test — no mocks, no external API keys or storage
needed, since video assembly is pure local compute. Generates synthetic test
images/audio with ffmpeg itself and runs the actual assembly/caption/music
pipeline end to end, verifying real, valid media comes out the other side."""

import os
import tempfile

import pytest

from src.workers.ffmpeg_utils import (
    _run,
    build_video_from_images_and_audio,
    burn_subtitles,
    generate_placeholder_music,
    mix_background_music,
    probe_duration_seconds,
)


@pytest.mark.asyncio
async def test_full_video_pipeline_produces_valid_output():
    with tempfile.TemporaryDirectory() as tmpdir:
        img1 = os.path.join(tmpdir, "img1.jpg")
        img2 = os.path.join(tmpdir, "img2.jpg")
        audio = os.path.join(tmpdir, "narration.mp3")

        await _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=640x360:d=1",
                "-frames:v",
                "1",
                img1,
            ]
        )
        await _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=640x360:d=1",
                "-frames:v",
                "1",
                img2,
            ]
        )
        await _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", audio])

        assembled = os.path.join(tmpdir, "assembled.mp4")
        await build_video_from_images_and_audio([img1, img2], [3.0, 3.0], audio, assembled)
        assembled_duration = await probe_duration_seconds(assembled)
        assert 5.5 < assembled_duration < 6.5

        srt_path = os.path.join(tmpdir, "captions.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(
                "1\n00:00:00,000 --> 00:00:03,000\nFirst scene\n\n"
                "2\n00:00:03,000 --> 00:00:06,000\nSecond scene\n"
            )

        captioned = os.path.join(tmpdir, "captioned.mp4")
        await burn_subtitles(assembled, srt_path, captioned)
        captioned_duration = await probe_duration_seconds(captioned)
        assert captioned_duration == pytest.approx(assembled_duration, abs=0.5)

        music_path = os.path.join(tmpdir, "music.mp3")
        await generate_placeholder_music(captioned_duration, music_path)

        final = os.path.join(tmpdir, "final.mp4")
        await mix_background_music(captioned, music_path, final)
        final_duration = await probe_duration_seconds(final)
        assert final_duration == pytest.approx(captioned_duration, abs=0.5)

        assert os.path.getsize(final) > 1000


@pytest.mark.asyncio
async def test_build_video_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        await build_video_from_images_and_audio(["a.jpg", "b.jpg"], [1.0], "audio.mp3", "out.mp4")
