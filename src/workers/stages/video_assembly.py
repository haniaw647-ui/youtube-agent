import os
import tempfile

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.storage import get_storage_provider
from src.providers.storage.base import StorageProvider
from src.workers.ffmpeg_utils import build_video_from_images_and_audio, probe_duration_seconds

# Matches script_writing.py's estimate — used here only to weight each scene's
# share of the real (probed) narration duration, not as an absolute value.
WORDS_PER_MINUTE = 150
MIN_SEGMENT_SECONDS = 1.5


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id, tenant_id FROM jobs WHERE id = :id"), {"id": job_id}
                )
            )
            .mappings()
            .one()
        )
        voice_asset = (
            (
                await session.execute(
                    text(
                        "SELECT storage_path FROM assets WHERE job_id = :job_id AND type = 'voice' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
        visual_assets = (
            (
                await session.execute(
                    text(
                        "SELECT segment_index, storage_path FROM assets "
                        "WHERE job_id = :job_id AND type = 'visual' ORDER BY segment_index"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .all()
        )
        script_stage = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'script_writing' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )

    if not visual_assets:
        raise RuntimeError(f"No visual assets found for job {job_id}")

    segment_by_scene = {s["scene"]: s for s in script_stage["output_ref"]["segments"]}
    storage = get_storage_provider()

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "narration.mp3")
        audio_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(voice_asset["storage_path"])
        )
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        image_paths = []
        estimated_durations = []
        scenes = []
        for va in visual_assets:
            img_path = os.path.join(tmpdir, f"segment_{va['segment_index']}.jpg")
            img_bytes = await storage.download_bytes(
                StorageProvider.key_from_storage_path(va["storage_path"])
            )
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            image_paths.append(img_path)
            scenes.append(va["segment_index"])
            narration = segment_by_scene.get(va["segment_index"], {}).get("narration", "")
            word_count = len(narration.split())
            estimated_durations.append(max(word_count / WORDS_PER_MINUTE * 60, MIN_SEGMENT_SECONDS))

        actual_audio_duration = await probe_duration_seconds(audio_path)
        total_estimated = sum(estimated_durations) or 1.0
        scale = actual_audio_duration / total_estimated
        durations = [max(d * scale, 1.0) for d in estimated_durations]

        output_path = os.path.join(tmpdir, "assembled.mp4")
        await build_video_from_images_and_audio(image_paths, durations, audio_path, output_path)

        with open(output_path, "rb") as f:
            video_bytes = f.read()

    key = f"{tenant_id}/{job_row['channel_id']}/{job_id}/render/assembled.mp4"
    storage_path = await storage.upload_bytes(key, video_bytes, "video/mp4")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO assets (tenant_id, job_id, type, storage_path) "
                "VALUES (:tenant_id, :job_id, 'video_draft', :storage_path)"
            ),
            {"tenant_id": job_row["tenant_id"], "job_id": job_id, "storage_path": storage_path},
        )
        await session.commit()

    return {
        "asset_type": "video_draft",
        "storage_path": storage_path,
        "total_duration_seconds": actual_audio_duration,
        # subtitle_burn_in reads this back to build exactly-matching SRT timing.
        "segment_durations": [
            {"scene": s, "duration": d} for s, d in zip(scenes, durations, strict=True)
        ],
    }
