import uuid

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.providers.llm.anthropic import AnthropicProvider
from src.workers.stages._json import parse_json_response

NUM_CANDIDATES = 5

SYSTEM_PROMPT = """You are a YouTube content strategist generating topic ideas for a channel.
Respond with ONLY a JSON array (no prose before or after) of exactly {n} topic candidates.
Each candidate must have exactly this shape:
{{
  "title": "the video title",
  "hook": "a one-sentence hook for the opening seconds",
  "angle": "what makes this specific take distinct",
  "audience": "who this is for",
  "estimated_interest": <int 0-100>,
  "uniqueness_score": <int 0-100>,
  "difficulty": <int 0-100, higher = more competitive/harder to stand out>,
  "evergreen": <true or false>
}}"""


def score_candidate(candidate: dict) -> float:
    """score = interest + uniqueness - difficulty, with an evergreen bonus.
    Simplified from the master prompt's five-dimension formula (interest +
    uniqueness + audience_relevance + search_potential + retention_potential -
    competition) to match the fields actually collected from the LLM — those
    extra dimensions aren't modeled as separate columns, and difficulty here
    plays the role competition does in the original formula."""
    score = candidate.get("estimated_interest", 0) + candidate.get("uniqueness_score", 0)
    score -= candidate.get("difficulty", 0)
    if candidate.get("evergreen"):
        score += 10
    return float(score)


async def run(job_id: str, tenant_id: str) -> dict:
    api_key = await get_tenant_key(tenant_id, "anthropic")
    llm = AnthropicProvider(api_key=api_key)

    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id, topic_brief FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
        channel_id = job_row["channel_id"]
        channel = (
            (
                await session.execute(
                    text(
                        "SELECT niche, audience, language, style, video_length_target_seconds "
                        "FROM channels WHERE id = :id"
                    ),
                    {"id": channel_id},
                )
            )
            .mappings()
            .one()
        )
        existing_titles = (
            (
                await session.execute(
                    text("SELECT title FROM topics WHERE channel_id = :id"), {"id": channel_id}
                )
            )
            .scalars()
            .all()
        )
        human_rejection = (
            (
                await session.execute(
                    text(
                        "SELECT notes FROM approvals WHERE job_id = :job_id "
                        "AND stage = 'topic_scoring' AND decision = 'rejected' "
                        "AND notes IS NOT NULL ORDER BY resolved_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )

    avoid = "\n".join(f"- {t}" for t in existing_titles) or "(none yet — this is the first topic)"
    user_prompt = (
        f"Channel niche: {channel['niche'] or 'general'}\n"
        f"Target audience: {channel['audience'] or 'general audience'}\n"
        f"Language: {channel['language']}\n"
        f"Style/tone: {channel['style'] or 'engaging, informative'}\n"
        f"Target video length: {channel['video_length_target_seconds'] or 600} seconds\n\n"
        f"Topics already covered on this channel — do not repeat these or close variants:\n"
        f"{avoid}\n\n"
        f"Generate {NUM_CANDIDATES} new topic candidates."
    )
    if job_row["topic_brief"]:
        # The creator specified a topic/theme when starting this job — this
        # overrides the generic niche-based brainstorm with a direct
        # instruction. Takes priority over rejection feedback below since a
        # topic_brief is a stronger, more specific signal than after-the-fact
        # notes on an AI-generated batch the creator didn't ask for.
        user_prompt += (
            f"\n\nThe creator requested this specific topic/theme for this video — "
            f"generate all {NUM_CANDIDATES} candidates centered on it, as different "
            f"specific angles/takes on the same request, not unrelated ideas:\n"
            f"{job_row['topic_brief']}\n"
        )
    elif human_rejection:
        # The creator rejected the last batch of candidates with specific
        # feedback on what to change — same reasoning as script_writing's
        # revision_feedback, just for the topic_scoring gate.
        user_prompt += (
            f"\n\nThe creator rejected the previous batch of candidates with this "
            f"feedback — address it directly:\n{human_rejection['notes']}\n"
        )

    raw = await llm.complete(system=SYSTEM_PROMPT.format(n=NUM_CANDIDATES), user=user_prompt)
    candidates = parse_json_response(raw)

    existing_lower = {t.lower().strip() for t in existing_titles}
    inserted = 0
    async with service_session() as session:
        for c in candidates:
            if c["title"].lower().strip() in existing_lower:
                continue
            await session.execute(
                text(
                    "INSERT INTO topics "
                    "(tenant_id, channel_id, job_id, title, hook, angle, audience, "
                    " estimated_interest, uniqueness_score, difficulty, evergreen, score, status) "
                    "VALUES (:tenant_id, :channel_id, :job_id, :title, :hook, :angle, :audience, "
                    " :estimated_interest, :uniqueness_score, :difficulty, :evergreen, :score, "
                    " 'candidate')"
                ),
                {
                    "tenant_id": uuid.UUID(str(tenant_id)),
                    "channel_id": channel_id,
                    "job_id": job_id,
                    "title": c["title"],
                    "hook": c.get("hook"),
                    "angle": c.get("angle"),
                    "audience": c.get("audience"),
                    "estimated_interest": c.get("estimated_interest", 0),
                    "uniqueness_score": c.get("uniqueness_score", 0),
                    "difficulty": c.get("difficulty", 0),
                    "evergreen": bool(c.get("evergreen", False)),
                    "score": score_candidate(c),
                },
            )
            inserted += 1
        await session.commit()

    return {"candidates_generated": len(candidates), "candidates_inserted": inserted}
