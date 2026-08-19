import os
import tempfile

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.provider_keys import MissingProviderKeyError, get_tenant_key
from src.orchestrator.storage import get_storage_provider
from src.providers.voice.elevenlabs import ElevenLabsProvider
from src.providers.voice.openai_tts import OpenAITTSProvider
from src.workers.ffmpeg_utils import generate_silence

# Which BYO key a channel's voice_over uses — configurable per channel via
# provider_config, defaulting to the premium option (ARCHITECTURE.md's "mixed:
# premium where it matters most" strategy — voice quality is one of those spots).
DEFAULT_PROVIDER = "elevenlabs"

# No voice provider connected is a real, expected state for a tenant still
# testing the pipeline (both ElevenLabs' free tier and Azure/OpenAI need a
# paid plan for API access — a genuine external constraint, not a bug here).
# Same reasoning as background_music.py's placeholder tone: keep the pipeline
# complete end-to-end rather than hard-failing, but make the gap impossible
# to miss via the license_type final_qa's audit_licenses() already checks.
PLACEHOLDER_LICENSE_TYPE = "platform-placeholder-not-for-production"
PLACEHOLDER_ATTRIBUTION = (
    "Silent placeholder narration — no voice provider connected. "
    "Connect a real ElevenLabs/OpenAI key before publishing."
)


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
        provider_config = (
            await session.execute(
                text("SELECT provider_config FROM channels WHERE id = :id"),
                {"id": job_row["channel_id"]},
            )
        ).scalar_one()
        script = (
            (
                await session.execute(
                    text(
                        "SELECT content, est_duration_seconds FROM scripts WHERE job_id = :job_id "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )

    provider_name = (provider_config or {}).get("voice_provider", DEFAULT_PROVIDER)

    try:
        api_key = await get_tenant_key(tenant_id, provider_name)
        voice = (
            ElevenLabsProvider(api_key)
            if provider_name == "elevenlabs"
            else OpenAITTSProvider(api_key)
        )
        audio_bytes = await voice.synthesize(script["content"])
        license_type = None
    except MissingProviderKeyError:
        provider_name = "placeholder-silence"
        duration = script["est_duration_seconds"] or 30
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "silence.mp3")
            await generate_silence(duration, audio_path)
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
        license_type = PLACEHOLDER_LICENSE_TYPE

    storage = get_storage_provider()
    key = f"{tenant_id}/{job_row['channel_id']}/{job_id}/voice/narration.mp3"
    storage_path = await storage.upload_bytes(key, audio_bytes, "audio/mpeg")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO assets "
                "(tenant_id, job_id, type, storage_path, provider, license_type) "
                "VALUES (:tenant_id, :job_id, 'voice', :storage_path, :provider, :license_type)"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "storage_path": storage_path,
                "provider": provider_name,
                "license_type": license_type,
            },
        )
        await session.commit()

    result = {"asset_type": "voice", "provider": provider_name, "storage_path": storage_path}
    if license_type:
        result["license_type"] = license_type
        result["attribution_text"] = PLACEHOLDER_ATTRIBUTION
    return result
