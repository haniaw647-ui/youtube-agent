import json

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import get_tenant_key
from src.providers.llm.anthropic import AnthropicProvider
from src.workers.stages._json import parse_json_response

# Total QA attempts allowed (1 initial check + up to this many revision cycles)
# before escalating to a human — ARCHITECTURE.md §4's approval-gate mechanism.
MAX_QA_ATTEMPTS = 3

SYSTEM_PROMPT = """You are a strict QA editor reviewing a YouTube script before it goes to
production. Check factual consistency against the research notes, tone/style match against
the channel's style, and pacing. Respond with ONLY JSON (no prose before or after):
{
  "passed": true or false,
  "feedback": "specific, actionable feedback — empty string if passed",
  "flags": ["specific issues found, empty list if none"]
}"""


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        script = (
            (
                await session.execute(
                    text(
                        "SELECT id, version, content FROM scripts WHERE job_id = :job_id "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
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
        channel = (
            (
                await session.execute(
                    text(
                        "SELECT c.style FROM jobs j JOIN channels c ON c.id = j.channel_id "
                        "WHERE j.id = :job_id"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
        # Includes the row execute_stage already inserted for this very run, so
        # this is this attempt's ordinal (1st check = 1, 1st revision check = 2, ...).
        attempt_number = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM job_stages WHERE job_id = :job_id AND stage = 'script_qa'"
                ),
                {"job_id": job_id},
            )
        ).scalar_one()

    research_notes = (research_row["output_ref"] if research_row else None) or {}
    user_prompt = (
        f"Channel style: {channel['style'] or 'engaging, informative'}\n\n"
        f"Research notes:\n{json.dumps(research_notes)[:3000]}\n\n"
        f"Script to review:\n{script['content']}"
    )

    api_key = await get_tenant_key(tenant_id, "anthropic")
    llm = AnthropicProvider(api_key=api_key)
    raw = await llm.complete(system=SYSTEM_PROMPT, user=user_prompt)
    result = parse_json_response(raw)

    async with service_session() as session:
        await session.execute(
            text("UPDATE scripts SET status = :status WHERE id = :id"),
            {
                "status": "approved" if result["passed"] else "revision_requested",
                "id": script["id"],
            },
        )
        await session.commit()

    if result["passed"]:
        return {"verdict": "pass", "feedback": result.get("feedback", "")}
    if attempt_number >= MAX_QA_ATTEMPTS:
        return {
            "verdict": "escalate",
            "feedback": result.get("feedback", ""),
            "flags": result.get("flags", []),
            "attempts": attempt_number,
        }
    return {
        "verdict": "revise",
        "feedback": result.get("feedback", ""),
        "flags": result.get("flags", []),
        "attempts": attempt_number,
    }
