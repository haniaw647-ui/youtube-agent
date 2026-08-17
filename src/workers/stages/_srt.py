def format_srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, ms = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(segment_durations: list[dict], segment_by_scene: dict) -> str:
    """segment_durations: [{"scene": int, "duration": float}, ...] in playback
    order. segment_by_scene: {scene: {"narration": str, ...}} from script_writing's
    output. Produces standard numbered SRT cue blocks with cumulative timing."""
    blocks = []
    cursor = 0.0
    for i, sd in enumerate(segment_durations, start=1):
        start, end = cursor, cursor + sd["duration"]
        narration = segment_by_scene.get(sd["scene"], {}).get("narration", "")
        blocks.append(
            f"{i}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{narration}\n"
        )
        cursor = end
    return "\n".join(blocks)
