import os
import tempfile

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.storage import get_storage_provider
from src.providers.storage.base import StorageProvider
from src.workers.ffmpeg_utils import (
    generate_placeholder_music,
    mix_background_music,
    probe_duration_seconds,
)

# No real licensed music library is wired up yet (API_REQUIREMENTS.md §2 calls
# for a curated/licensed set — sourcing one is a content decision, not
# something to fabricate). This placeholder tone keeps the pipeline complete
# end-to-end while making the gap impossible to miss: it's stamped on the
# asset's license_type, which Final QA (Phase 6) checks before anything can
# reach a real upload.
PLACEHOLDER_LICENSE_TYPE = "platform-placeholder-not-for-production"
PLACEHOLDER_ATTRIBUTION = (
    "Synthesized placeholder tone — not licensed for production use. "
    "Replace with a real curated/licensed music library before publishing."
)


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
        captions_stage = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'subtitle_burn_in' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )

    storage = get_storage_provider()
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "with_captions.mp4")
        video_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(captions_stage["output_ref"]["storage_path"])
        )
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        duration = await probe_duration_seconds(video_path)
        music_path = os.path.join(tmpdir, "music.mp3")
        await generate_placeholder_music(duration, music_path)

        output_path = os.path.join(tmpdir, "final.mp4")
        await mix_background_music(video_path, music_path, output_path)

        with open(output_path, "rb") as f:
            final_bytes = f.read()

    key = f"{tenant_id}/{job_row['channel_id']}/{job_id}/render/final.mp4"
    storage_path = await storage.upload_bytes(key, final_bytes, "video/mp4")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO assets (tenant_id, job_id, type, storage_path, license_type) "
                "VALUES (:tenant_id, :job_id, 'video_final', :storage_path, :license_type)"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "storage_path": storage_path,
                "license_type": PLACEHOLDER_LICENSE_TYPE,
            },
        )
        await session.commit()

    return {
        "asset_type": "video_final",
        "storage_path": storage_path,
        "license_type": PLACEHOLDER_LICENSE_TYPE,
        "attribution_text": PLACEHOLDER_ATTRIBUTION,
    }
