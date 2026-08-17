from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.orchestrator.storage import get_storage_provider
from src.providers.visual.pexels import PexelsProvider
from src.workers.stages._http import download
from src.workers.thumbnail_utils import compose_thumbnail

# Same reasoning as visual_generation: stock is the default, generative image
# providers are opt-in and not wired up until a real tenant needs one.
DEFAULT_PROVIDER = "pexels"


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
        topic = (
            (
                await session.execute(
                    text(
                        "SELECT t.title, t.hook FROM topics t JOIN jobs j ON j.topic_id = t.id "
                        "WHERE j.id = :job_id"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )

    api_key = await get_tenant_key(tenant_id, DEFAULT_PROVIDER)
    visual = PexelsProvider(api_key)
    results = await visual.search(topic["title"], count=1)
    if not results:
        raise RuntimeError(f"No stock image found for thumbnail base, job {job_id}")
    result = results[0]

    base_image_bytes = await download(result.url)
    overlay_text = topic["hook"] or topic["title"]
    thumbnail_bytes = compose_thumbnail(base_image_bytes, overlay_text)

    storage = get_storage_provider()
    key = f"{tenant_id}/{job_row['channel_id']}/{job_id}/thumbnail/candidate_1.png"
    storage_path = await storage.upload_bytes(key, thumbnail_bytes, "image/png")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO assets "
                "(tenant_id, job_id, type, storage_path, provider, license_type) "
                "VALUES (:tenant_id, :job_id, 'thumbnail', :storage_path, :provider, "
                " :license_type)"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "storage_path": storage_path,
                "provider": result.source,
                "license_type": result.license_type,
            },
        )
        await session.commit()

    return {"asset_type": "thumbnail", "storage_path": storage_path}
