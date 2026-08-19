"""Real, unmocked apart from ffmpeg — proves voice_over falls back to a
silent placeholder track (instead of hard-failing the whole job) when a
tenant has no usable voice provider key, and that the placeholder is
correctly stamped with a license_type final_qa's audit_licenses() already
treats as unresolved (so it can never silently slip past to a real
YouTube upload without a human noticing). Real trigger for this: both
ElevenLabs' free tier and Azure/OpenAI need a paid plan for API access —
a genuine external constraint a tenant can hit on day one.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.workers.stages.voice_over import PLACEHOLDER_LICENSE_TYPE, run

TENANT = uuid.uuid4()


def setup_module(_module: object) -> None:
    from src.orchestrator.tenants import ensure_tenant

    asyncio.run(ensure_tenant(TENANT, "Voice Placeholder Test Tenant"))


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM assets WHERE job_id IN (SELECT id FROM jobs WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await session.execute(text("DELETE FROM scripts WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


async def _seed_job_with_script(job_id: str) -> None:
    async with service_session() as session:
        channel_id = (
            await session.execute(
                text(
                    "INSERT INTO channels (tenant_id, name, approval_gates) "
                    "VALUES (:t, 'Voice Placeholder Channel', '{}') RETURNING id"
                ),
                {"t": TENANT},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :cid, 'voice_over', 'running')"
            ),
            {"id": job_id, "t": TENANT, "cid": channel_id},
        )
        await session.execute(
            text(
                "INSERT INTO scripts (job_id, content, est_duration_seconds, tenant_id) "
                "VALUES (:jid, 'Hello world, this is a test script.', 5, :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.commit()


def test_falls_back_to_silent_placeholder_when_no_voice_key_connected() -> None:
    job_id = f"job_voice_placeholder_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_script(job_id))

    with patch(
        "src.workers.stages.voice_over.generate_silence", new=AsyncMock()
    ) as mock_silence:

        async def _fake_generate_silence(duration: float, output_path: str) -> None:
            with open(output_path, "wb") as f:
                f.write(b"fake-silent-mp3-bytes")

        mock_silence.side_effect = _fake_generate_silence
        result = asyncio.run(run(job_id, str(TENANT)))

    assert result["provider"] == "placeholder-silence"
    assert result["license_type"] == PLACEHOLDER_LICENSE_TYPE
    mock_silence.assert_awaited_once()
    assert mock_silence.await_args.args[0] == 5  # est_duration_seconds from the script

    async def _check_asset() -> None:
        async with service_session() as session:
            asset = (
                await session.execute(
                    text(
                        "SELECT provider, license_type FROM assets "
                        "WHERE job_id = :jid AND type = 'voice'"
                    ),
                    {"jid": job_id},
                )
            ).mappings().one()
            assert asset["provider"] == "placeholder-silence"
            assert asset["license_type"] == PLACEHOLDER_LICENSE_TYPE

    asyncio.run(_check_asset())
