import os
import tempfile

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.storage import get_storage_provider
from src.providers.storage.base import StorageProvider
from src.workers.ffmpeg_utils import burn_subtitles
from src.workers.stages._srt import build_srt


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id FROM jobs WHERE id = :id"), {"id": job_id}
                )
            )
            .mappings()
            .one()
        )
        video_asset = (
            (
                await session.execute(
                    text(
                        "SELECT storage_path FROM assets WHERE job_id = :job_id "
                        "AND type = 'video_draft' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
        assembly_stage = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'video_assembly' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
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

    segment_durations = assembly_stage["output_ref"]["segment_durations"]
    segment_by_scene = {s["scene"]: s for s in script_stage["output_ref"]["segments"]}
    srt_content = build_srt(segment_durations, segment_by_scene)

    storage = get_storage_provider()
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "assembled.mp4")
        video_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(video_asset["storage_path"])
        )
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        srt_path = os.path.join(tmpdir, "captions.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        output_path = os.path.join(tmpdir, "with_captions.mp4")
        await burn_subtitles(video_path, srt_path, output_path)

        with open(output_path, "rb") as f:
            captioned_bytes = f.read()

    base_key = f"{tenant_id}/{job_row['channel_id']}/{job_id}/render"
    video_storage_path = await storage.upload_bytes(
        f"{base_key}/with_captions.mp4", captioned_bytes, "video/mp4"
    )
    srt_storage_path = await storage.upload_bytes(
        f"{base_key}/captions.srt", srt_content.encode("utf-8"), "text/plain"
    )

    return {"storage_path": video_storage_path, "srt_storage_path": srt_storage_path}
