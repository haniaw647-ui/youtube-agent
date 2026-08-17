import httpx

from src.providers.voice.base import VoiceProvider

DEFAULT_VOICE = "alloy"


class OpenAITTSProvider(VoiceProvider):
    def __init__(self, api_key: str, voice: str = DEFAULT_VOICE):
        self._api_key = api_key
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": "tts-1", "input": text, "voice": self._voice},
            )
        resp.raise_for_status()
        return resp.content
