from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Every LLM adapter implements this — swapping providers is a config
    change plus one new adapter, never a rewrite of stage logic."""

    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        """Returns the raw text completion. Callers that need structured output
        are responsible for prompting for a specific format (e.g. JSON) and
        parsing the result themselves — kept out of this interface so it stays
        provider-agnostic."""
        raise NotImplementedError
