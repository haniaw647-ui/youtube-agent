from abc import ABC, abstractmethod


class VoiceProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Returns raw audio bytes (mp3)."""
        raise NotImplementedError
