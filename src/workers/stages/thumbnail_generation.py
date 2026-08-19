import re

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import MissingProviderKeyError, get_tenant_key
from src.orchestrator.storage import get_storage_provider
from src.providers.visual.base import VisualProvider
from src.providers.visual.pexels import PexelsProvider
from src.providers.visual.pixabay import PixabayProvider
from src.workers.stages._http import download
from src.workers.thumbnail_utils import compose_thumbnail

# Same reasoning as visual_generation: stock is the default, generative image
# providers are opt-in and not wired up until a real tenant needs one. Same
# Pexels->Pixabay fallback too — confirmed live that a tenant with only a
# Pixabay key hard-failed this stage with no fallback, even though
# visual_generation.py already handled the identical situation.
DEFAULT_PROVIDER = "pexels"
FALLBACK_PROVIDER = "pixabay"

# Pixabay rejects search queries over 100 chars outright (400 Bad Request) —
# same real bug already hit and fixed in visual_generation.py.
_MAX_QUERY_LENGTH = 100

# Confirmed live against Pixabay: it AND-matches every word in the query, so
# a full clickbait video title ("I Let Captchas Decide My Day (Click All the
# Squares With Traffic Lights)") returns zero hits even though the same
# provider has hundreds of hits for its actual subject. Titles are written to
# hook a viewer, not to describe a photographable scene, so they're
# unreliable stock-photo search terms. Falls back to the hook, then a
# stopword-stripped keyword extraction, then a generic query that always
# returns something — a thumbnail's base image doesn't need to be a precise
# match, the overlay text carries the actual title.
_STOPWORDS = {
    "i", "a", "an", "the", "my", "your", "his", "her", "its", "our", "their",
    "let", "to", "of", "in", "on", "for", "and", "or", "is", "are", "this",
    "that", "with", "all",
}
_GENERIC_FALLBACK_QUERY = "background"


def _keyword_query(text_value: str, max_words: int = 4) -> str:
    without_parens = re.sub(r"\([^)]*\)", "", text_value)
    words = re.findall(r"[A-Za-z]+", without_parens)
    keywords = [w for w in words if w.lower() not in _STOPWORDS][:max_words]
    return " ".join(keywords)


def _thumbnail_search_queries(title: str, hook: str | None) -> list[str]:
    candidates = [title, hook or "", _keyword_query(title), _GENERIC_FALLBACK_QUERY]
    queries = []
    for c in candidates:
        q = c[:_MAX_QUERY_LENGTH].strip()
        if q and q not in queries:
            queries.append(q)
    return queries


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

    try:
        provider_name = DEFAULT_PROVIDER
        api_key = await get_tenant_key(tenant_id, provider_name)
    except MissingProviderKeyError:
        provider_name = FALLBACK_PROVIDER
        api_key = await get_tenant_key(tenant_id, provider_name)
    visual: VisualProvider = (
        PexelsProvider(api_key) if provider_name == "pexels" else PixabayProvider(api_key)
    )
    results = []
    for query in _thumbnail_search_queries(topic["title"], topic["hook"]):
        results = await visual.search(query, count=1)
        if results:
            break
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
