"""Proves voice_over's full fallback chain when a tenant has no paid voice
provider key: real ElevenLabs/OpenAI provider -> free edge-tts (real,
unmocked network call — genuinely free/keyless, fast enough to exercise for
real rather than mock) -> silent placeholder only if edge-tts itself also
fails. Confirms each tier is stamped with the license_type final_qa's
audit_licenses() expects (only the true silent-placeholder tier is treated
as unresolved)."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import edge_tts.exceptions
from sqlalchemy import text

from src.orchestrator.db import service_session
from src.workers.stages.voice_over import FALLBACK_LICENSE_TYPE, PLACEHOLDER_LICENSE_TYPE, run

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


async def _asset_row(job_id: str) -> dict:
    async with service_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT provider, license_type FROM assets "
                    "WHERE job_id = :jid AND type = 'voice'"
                ),
                {"jid": job_id},
            )
        ).mappings().one()
        return dict(row)


def test_falls_back_to_real_edge_tts_when_no_paid_voice_key_connected() -> None:
    job_id = f"job_voice_edge_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_script(job_id))

    result = asyncio.run(run(job_id, str(TENANT)))

    assert result["provider"] == "edge-tts"
    assert result["license_type"] == FALLBACK_LICENSE_TYPE
    assert "unofficial" in result["attribution_text"]

    asset = asyncio.run(_asset_row(job_id))
    assert asset["provider"] == "edge-tts"
    assert asset["license_type"] == FALLBACK_LICENSE_TYPE


def test_falls_back_to_silent_placeholder_when_edge_tts_also_fails() -> None:
    job_id = f"job_voice_placeholder_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_script(job_id))

    with patch(
        "src.workers.stages.voice_over.EdgeTTSProvider.synthesize",
        new=AsyncMock(side_effect=edge_tts.exceptions.NoAudioReceived("boom")),
    ), patch(
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

    asset = asyncio.run(_asset_row(job_id))
    assert asset["provider"] == "placeholder-silence"
    assert asset["license_type"] == PLACEHOLDER_LICENSE_TYPE
