from sqlalchemy import text

from src.orchestrator.db import service_session


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        candidates = (
            (
                await session.execute(
                    text(
                        "SELECT id, score FROM topics WHERE job_id = :job_id "
                        "AND status = 'candidate' ORDER BY score DESC"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .all()
        )

        if not candidates:
            raise RuntimeError(f"No topic candidates found for job {job_id}")

        selected = candidates[0]
        await session.execute(
            text("UPDATE topics SET status = 'selected' WHERE id = :id"), {"id": selected["id"]}
        )
        for rejected in candidates[1:]:
            await session.execute(
                text("UPDATE topics SET status = 'rejected' WHERE id = :id"), {"id": rejected["id"]}
            )
        await session.execute(
            text("UPDATE jobs SET topic_id = :topic_id WHERE id = :job_id"),
            {"topic_id": selected["id"], "job_id": job_id},
        )
        await session.commit()

    return {"selected_topic_id": str(selected["id"]), "score": float(selected["score"])}
