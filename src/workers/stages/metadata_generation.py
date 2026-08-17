import json

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.providers.llm.anthropic import AnthropicProvider
from src.workers.stages._json import parse_json_response

SYSTEM_PROMPT = """You are a YouTube SEO/metadata specialist. Given a topic, script, and
channel context, generate publish-ready metadata. Respond with ONLY JSON (no prose before
or after):
{
  "title_candidates": ["...", "...", "..."],
  "description": "2-4 short paragraphs, accurate to the script content",
  "tags": ["10-15 relevant single or short-phrase keywords"]
}
Title candidates should be under 100 characters, attention-grabbing but accurate to the
content — no clickbait that the script doesn't deliver on. If required attribution text is
given, include it verbatim at the end of the description, on its own line."""


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
        channel = (
            (
                await session.execute(
                    text("SELECT niche, audience, style, language FROM channels WHERE id = :id"),
                    {"id": job_row["channel_id"]},
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
        script = (
            (
                await session.execute(
                    text(
                        "SELECT content FROM scripts WHERE job_id = :job_id "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
        music_stage = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'background_music' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )

    attribution = ""
    if music_stage and music_stage["output_ref"]:
        attribution = music_stage["output_ref"].get("attribution_text", "")

    user_prompt = (
        f"Topic: {topic['title']}\n"
        f"Hook: {topic['hook']}\n"
        f"Channel niche: {channel['niche'] or 'general'}\n"
        f"Channel style: {channel['style'] or 'engaging, informative'}\n"
        f"Language: {channel['language']}\n\n"
        f"Script:\n{script['content'][:4000]}\n\n"
        f"Required attribution to include verbatim (empty if none): {attribution}\n"
    )

    api_key = await get_tenant_key(tenant_id, "anthropic")
    llm = AnthropicProvider(api_key=api_key)
    raw = await llm.complete(system=SYSTEM_PROMPT, user=user_prompt, max_tokens=1500)
    parsed = parse_json_response(raw)

    title = parsed["title_candidates"][0]
    description = parsed["description"]
    tags = parsed["tags"]

    async with service_session() as session:
        await session.execute(
            text(
                "UPDATE jobs SET title = :title, description = :description, tags = :tags "
                "WHERE id = :job_id"
            ),
            {
                "title": title,
                "description": description,
                "tags": json.dumps(tags),
                "job_id": job_id,
            },
        )
        await session.commit()

    return {"title": title, "title_candidates": parsed["title_candidates"], "tags": tags}
