import json

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.providers.llm.anthropic import AnthropicProvider
from src.workers.stages._json import parse_json_response

WORDS_PER_MINUTE = 150

SYSTEM_PROMPT = """You are a professional YouTube scriptwriter. Write a complete,
scene-segmented narration script for the given topic and research notes.
Respond with ONLY JSON (no prose before or after):
{
  "segments": [
    {"scene": 1, "narration": "the exact words to be spoken", "visual_note": "what's on screen"},
    ...
  ]
}
Each segment's narration should be a natural spoken paragraph, not bullet points."""


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id, topic_id FROM jobs WHERE id = :id"), {"id": job_id}
                )
            )
            .mappings()
            .one()
        )
        channel = (
            (
                await session.execute(
                    text(
                        "SELECT style, language, video_length_target_seconds "
                        "FROM channels WHERE id = :id"
                    ),
                    {"id": job_row["channel_id"]},
                )
            )
            .mappings()
            .one()
        )
        topic = (
            (
                await session.execute(
                    text("SELECT title, hook, angle FROM topics WHERE id = :id"),
                    {"id": job_row["topic_id"]},
                )
            )
            .mappings()
            .one()
        )
        research_row = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'research' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )
        latest_qa = (
            (
                await session.execute(
                    text(
                        "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                        "AND stage = 'script_qa' ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )
        prior_version = (
            await session.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM scripts WHERE job_id = :job_id"),
                {"job_id": job_id},
            )
        ).scalar_one()

    research_notes = (research_row["output_ref"] if research_row else None) or {}
    revision_feedback = None
    if latest_qa and latest_qa["output_ref"] and latest_qa["output_ref"].get("verdict") == "revise":
        revision_feedback = latest_qa["output_ref"].get("feedback")

    target_seconds = channel["video_length_target_seconds"] or 600
    target_words = int(target_seconds / 60 * WORDS_PER_MINUTE)

    user_prompt = (
        f"Topic: {topic['title']}\n"
        f"Hook: {topic['hook']}\n"
        f"Angle: {topic['angle']}\n"
        f"Language: {channel['language']}\n"
        f"Style/tone: {channel['style'] or 'engaging, informative'}\n"
        f"Target length: ~{target_words} words "
        f"(~{target_seconds}s at {WORDS_PER_MINUTE} wpm)\n\n"
        f"Research notes:\n{json.dumps(research_notes)[:3000]}\n"
    )
    if revision_feedback:
        user_prompt += f"\nThis is a revision. Address this QA feedback:\n{revision_feedback}\n"

    api_key = await get_tenant_key(tenant_id, "anthropic")
    llm = AnthropicProvider(api_key=api_key)
    raw = await llm.complete(system=SYSTEM_PROMPT, user=user_prompt, max_tokens=4000)
    parsed = parse_json_response(raw)
    segments = parsed["segments"]

    content = "\n\n".join(f"[Scene {s['scene']}] {s['narration']}" for s in segments)
    word_count = len(content.split())
    version = prior_version + 1

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO scripts "
                "(tenant_id, job_id, version, content, word_count, est_duration_seconds, status) "
                "VALUES ((SELECT tenant_id FROM jobs WHERE id = :job_id), :job_id, :version, "
                " :content, :word_count, :est_duration_seconds, 'draft')"
            ),
            {
                "job_id": job_id,
                "version": version,
                "content": content,
                "word_count": word_count,
                "est_duration_seconds": int(word_count / WORDS_PER_MINUTE * 60),
            },
        )
        await session.commit()

    return {"version": version, "word_count": word_count, "revised": revision_feedback is not None}
