from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import MissingProviderKeyError, get_tenant_key
from src.orchestrator.storage import get_storage_provider
from src.providers.visual.base import VisualProvider
from src.providers.visual.pexels import PexelsProvider
from src.providers.visual.pixabay import PixabayProvider
from src.workers.stages._http import download

# Stock is the default for every channel — free, zero licensing ambiguity
# (API_REQUIREMENTS.md §2). Generative visuals are opt-in, added only when a
# real tenant needs them (ARCHITECTURE.md §15's deliberate scope cut).
DEFAULT_PROVIDER = "pexels"
FALLBACK_PROVIDER = "pixabay"


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

    segments = script_stage["output_ref"]["segments"]

    try:
        provider_name = DEFAULT_PROVIDER
        api_key = await get_tenant_key(tenant_id, provider_name)
    except MissingProviderKeyError:
        provider_name = FALLBACK_PROVIDER
        api_key = await get_tenant_key(tenant_id, provider_name)
    visual: VisualProvider = (
        PexelsProvider(api_key) if provider_name == "pexels" else PixabayProvider(api_key)
    )
    storage = get_storage_provider()

    inserted = 0
    async with service_session() as session:
        for segment in segments:
            query = segment.get("visual_note") or segment.get("narration", "")[:100]
            results = await visual.search(query, count=1)
            if not results:
                continue
            result = results[0]
            image_bytes = await download(result.url)

            key = (
                f"{tenant_id}/{job_row['channel_id']}/{job_id}/visuals/"
                f"segment_{segment['scene']}.jpg"
            )
            storage_path = await storage.upload_bytes(key, image_bytes, "image/jpeg")

            await session.execute(
                text(
                    "INSERT INTO assets "
                    "(tenant_id, job_id, type, segment_index, storage_path, provider, "
                    " license_type) "
                    "VALUES (:tenant_id, :job_id, 'visual', :segment_index, :storage_path, "
                    " :provider, :license_type)"
                ),
                {
                    "tenant_id": job_row["tenant_id"],
                    "job_id": job_id,
                    "segment_index": segment["scene"],
                    "storage_path": storage_path,
                    "provider": result.source,
                    "license_type": result.license_type,
                },
            )
            inserted += 1
        await session.commit()

    return {"asset_type": "visual", "provider": provider_name, "segments_generated": inserted}
