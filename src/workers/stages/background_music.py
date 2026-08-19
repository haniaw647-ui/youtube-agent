import logging
import os
import tempfile

import httpx
from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.storage import get_storage_provider
from src.providers.storage.base import StorageProvider
from src.workers.ffmpeg_utils import (
    generate_placeholder_music,
    mix_background_music,
    probe_duration_seconds,
)
from src.workers.stages._http import download

logger = logging.getLogger(__name__)

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

# A tenant supplying their own track's URL is taking responsibility for its
# licensing themselves — distinct from PLACEHOLDER_LICENSE_TYPE, which is
# what final_qa's audit_licenses() specifically watches for, so a tenant-
# supplied track does NOT force the same review gate a placeholder would.
TENANT_PROVIDED_LICENSE_TYPE = "tenant-provided-url"


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
        channel_row = (
            (
                await session.execute(
                    text("SELECT background_music_url FROM channels WHERE id = :id"),
                    {"id": job_row["channel_id"]},
                )
            )
            .mappings()
            .one()
        )

    storage = get_storage_provider()
    license_type = PLACEHOLDER_LICENSE_TYPE
    attribution = PLACEHOLDER_ATTRIBUTION
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "with_captions.mp4")
        video_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(captions_stage["output_ref"]["storage_path"])
        )
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        duration = await probe_duration_seconds(video_path)
        music_path = os.path.join(tmpdir, "music.mp3")
        music_url = channel_row["background_music_url"]
        if music_url:
            try:
                music_bytes = await download(music_url)
                with open(music_path, "wb") as f:
                    f.write(music_bytes)
                license_type = TENANT_PROVIDED_LICENSE_TYPE
                attribution = f"Tenant-supplied track: {music_url}"
            except (httpx.HTTPError, OSError):
                logger.warning(
                    "background_music: failed to download tenant music_url for job %s, "
                    "falling back to the placeholder tone",
                    job_id,
                    exc_info=True,
                )
                await generate_placeholder_music(duration, music_path)
        else:
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
                "license_type": license_type,
            },
        )
        await session.commit()

    return {
        "asset_type": "video_final",
        "storage_path": storage_path,
        "license_type": license_type,
        "attribution_text": attribution,
    }
