from anthropic import AsyncAnthropic

from src.providers.llm.base import LLMProvider

# Pinned, not "latest" — reproducibility matters for script quality tuning
# (ENVIRONMENT.md notes this same principle for ANTHROPIC_MODEL_SCRIPT).
DEFAULT_MODEL = "claude-opus-4-5"


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
