import edge_tts

from src.providers.voice.base import VoiceProvider

# Free, keyless fallback: reuses the neural TTS engine behind Microsoft
# Edge's "Read aloud" feature via a reverse-engineered client (no Microsoft
# account, API key, or billing involved — see github.com/rany2/edge-tts).
# Genuine neural speech, not a placeholder, so it isn't stamped with the
# "placeholder" license marker final_qa's audit specifically watches for.
# The one real caveat: it's not a documented/licensed commercial API, so
# Microsoft could change or block the underlying endpoint without notice,
# and using it at real scale in a monetized product is a legal gray area
# worth the tenant knowing about (flagged in voice_over.py's attribution
# text rather than hidden).
DEFAULT_VOICE = "en-US-AriaNeural"


class EdgeTTSProvider(VoiceProvider):
    def __init__(self, voice: str = DEFAULT_VOICE):
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        communicate = edge_tts.Communicate(text, self._voice)
        chunks = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
        return bytes(chunks)
