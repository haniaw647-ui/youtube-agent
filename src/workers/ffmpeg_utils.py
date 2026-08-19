import asyncio
import json
import os
import tempfile

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"

# libx264 auto-detects thread count from the host machine's core count, not
# the container's cgroup CPU quota — on Railway that meant 34 encoder threads
# fighting over a 2-vCPU limit, starving the process until it got OOM/signal
# killed with no ffmpeg error message (just silence at frame=0). Every
# re-encoding _run() call below caps -threads explicitly to match the actual
# quota.
ENCODE_THREADS = "2"

# The default "medium" preset's multi-frame lookahead/mbtree analysis was
# still enough to peg the 1GB container's memory near its ceiling and stall
# re-encodes at ~0.01x realtime even with threads capped. "ultrafast" cuts
# that buffering to the minimum — draft-quality output is fine here since
# every render is a placeholder pending human final_qa review anyway.
ENCODE_PRESET = "ultrafast"


class FfmpegError(Exception):
    pass


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(stderr.decode(errors="replace")[-2000:])


async def probe_duration_seconds(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(stderr.decode(errors="replace")[-2000:])
    return float(json.loads(stdout)["format"]["duration"])


async def probe_video_info(path: str) -> dict:
    """Resolution + stream presence, for final_qa's sync/aspect-ratio checks."""
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "stream=width,height,codec_type:format=duration",
        "-of",
        "json",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(stderr.decode(errors="replace")[-2000:])
    data = json.loads(stdout)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "width": video_stream.get("width") if video_stream else None,
        "height": video_stream.get("height") if video_stream else None,
        "has_video": video_stream is not None,
        "has_audio": has_audio,
        "duration": float(data.get("format", {}).get("duration", 0)),
    }


def _escape_filter_path(path: str) -> str:
    """ffmpeg filtergraph args use ':' and other chars as separators — needed
    for the subtitles filter specifically — do not use this for the concat
    demuxer's file list, which uses plain (optionally quoted) paths and would
    be corrupted by colon-escaping."""
    escaped = path.replace("\\", "/").replace(":", r"\:")
    return escaped


def _posix_path(path: str) -> str:
    """Forward-slash form for the concat demuxer's file list — no colon
    escaping, that syntax belongs only to filtergraph string arguments."""
    return path.replace("\\", "/")


async def build_video_from_images_and_audio(
    image_paths: list[str], durations: list[float], audio_path: str, output_path: str
) -> None:
    """One Ken-Burns-style slow zoom clip per image, held for that image's
    duration, concatenated, with the narration audio laid over the top."""
    if len(image_paths) != len(durations):
        raise ValueError("image_paths and durations must be the same length")

    with tempfile.TemporaryDirectory() as tmpdir:
        clip_paths = []
        for i, (image_path, duration) in enumerate(zip(image_paths, durations, strict=True)):
            clip_path = os.path.join(tmpdir, f"clip_{i}.mp4")
            frames = max(int(duration * 25), 1)
            await _run(
                [
                    FFMPEG_BIN,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    image_path,
                    "-vf",
                    (
                        "scale=1920:1080:force_original_aspect_ratio=increase,"
                        "crop=1920:1080,"
                        f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}:s=1920x1080:fps=25"
                    ),
                    "-t",
                    str(duration),
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    ENCODE_THREADS,
                    "-preset",
                    ENCODE_PRESET,
                    clip_path,
                ]
            )
            clip_paths.append(clip_path)

        concat_list_path = os.path.join(tmpdir, "concat.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for p in clip_paths:
                f.write(f"file '{_posix_path(p)}'\n")

        silent_video_path = os.path.join(tmpdir, "silent.mp4")
        await _run(
            [
                FFMPEG_BIN,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list_path,
                "-c",
                "copy",
                silent_video_path,
            ]
        )

        await _run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                silent_video_path,
                "-i",
                audio_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                output_path,
            ]
        )


async def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    await _run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-vf",
            f"subtitles='{_escape_filter_path(srt_path)}'",
            "-c:a",
            "copy",
            "-threads",
            ENCODE_THREADS,
            "-preset",
            ENCODE_PRESET,
            output_path,
        ]
    )


async def generate_silence(duration_seconds: float, output_path: str) -> None:
    """A silent audio track standing in for real narration when no voice
    provider is configured — same reasoning as generate_placeholder_music,
    see the license_type stamped on its asset row by voice_over.py."""
    await _run(
        [
            FFMPEG_BIN,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=mono:duration={duration_seconds}",
            "-q:a",
            "9",
            output_path,
        ]
    )


async def generate_placeholder_music(duration_seconds: float, output_path: str) -> None:
    """A synthesized ambient tone standing in for a real curated/licensed music
    library (API_REQUIREMENTS.md §2) until one is actually sourced. Never treat
    this as production-ready — see the license_type stamped on its asset row."""
    await _run(
        [
            FFMPEG_BIN,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={duration_seconds}",
            "-af",
            "volume=0.05",
            output_path,
        ]
    )


async def mix_background_music(
    video_path: str, music_path: str, output_path: str, music_volume: float = 0.15
) -> None:
    await _run(
        [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-i",
            music_path,
            "-filter_complex",
            (
                f"[1:a]volume={music_volume},aloop=loop=-1:size=2e9[music];"
                "[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            output_path,
        ]
    )
