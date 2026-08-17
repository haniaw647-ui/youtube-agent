import os
import tempfile

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.storage import get_storage_provider
from src.providers.storage.base import StorageProvider
from src.workers.ffmpeg_utils import probe_video_info
from src.workers.stages._final_qa_checks import evaluate_checklist


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT title, description, tags FROM jobs WHERE id = :id"), {"id": job_id}
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
                        "AND type = 'video_final' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )
        all_assets = (
            (
                await session.execute(
                    text("SELECT type, license_type FROM assets WHERE job_id = :job_id"),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .all()
        )
        latest_qa = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'script_qa' AND status = 'done' "
                        "ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )

    if video_asset is None:
        raise RuntimeError(f"No final video asset found for job {job_id}")

    storage = get_storage_provider()
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "final.mp4")
        video_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(video_asset["storage_path"])
        )
        with open(video_path, "wb") as f:
            f.write(video_bytes)
        video_info = await probe_video_info(video_path)

    content_flags = []
    if latest_qa and latest_qa["output_ref"]:
        content_flags = latest_qa["output_ref"].get("flags", [])

    return evaluate_checklist(
        video_info=video_info,
        title=job_row["title"],
        description=job_row["description"],
        tags=job_row["tags"],
        assets=[dict(a) for a in all_assets],
        content_flags=content_flags,
    )
