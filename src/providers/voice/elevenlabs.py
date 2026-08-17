import httpx

from src.providers.voice.base import VoiceProvider

# "Rachel" — one of ElevenLabs' standard premade voices, usable out of the box
# without per-tenant voice cloning/selection setup. Channels can override via
# provider_config once voice selection UI exists.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


class ElevenLabsProvider(VoiceProvider):
    def __init__(self, api_key: str, voice_id: str = DEFAULT_VOICE_ID):
        self._api_key = api_key
        self._voice_id = voice_id

    async def synthesize(self, text: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}",
                headers={"xi-api-key": self._api_key, "Accept": "audio/mpeg"},
                json={"text": text, "model_id": "eleven_multilingual_v2"},
            )
        resp.raise_for_status()
        return resp.content
