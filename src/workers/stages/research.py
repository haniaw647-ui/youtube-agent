from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.providers.search.tavily import TavilyProvider


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        topic = (
            (
                await session.execute(
                    text(
                        "SELECT t.title, t.hook, t.angle FROM topics t "
                        "JOIN jobs j ON j.topic_id = t.id WHERE j.id = :job_id"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )

    api_key = await get_tenant_key(tenant_id, "tavily")
    search = TavilyProvider(api_key=api_key)
    results = await search.search(topic["title"], max_results=5)

    return {
        "query": topic["title"],
        "sources": [{"title": r.title, "url": r.url, "snippet": r.snippet[:500]} for r in results],
    }
