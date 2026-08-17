from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.llm.anthropic import AnthropicProvider
from src.providers.search.tavily import TavilyProvider


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeResponse:
    content: list


@pytest.mark.asyncio
async def test_anthropic_provider_extracts_text_from_response():
    provider = AnthropicProvider(api_key="sk-fake-not-real")
    fake_response = _FakeResponse(content=[_FakeBlock(type="text", text="hello world")])
    provider._client.messages.create = AsyncMock(return_value=fake_response)

    result = await provider.complete(system="be nice", user="say hi")

    assert result == "hello world"
    provider._client.messages.create.assert_awaited_once()
    _, kwargs = provider._client.messages.create.await_args
    assert kwargs["system"] == "be nice"
    assert kwargs["messages"] == [{"role": "user", "content": "say hi"}]


@pytest.mark.asyncio
async def test_anthropic_provider_ignores_non_text_blocks():
    provider = AnthropicProvider(api_key="sk-fake-not-real")
    fake_response = _FakeResponse(
        content=[_FakeBlock(type="tool_use", text=""), _FakeBlock(type="text", text="only this")]
    )
    provider._client.messages.create = AsyncMock(return_value=fake_response)

    result = await provider.complete(system="s", user="u")

    assert result == "only this"


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_tavily_provider_parses_results():
    payload = {
        "results": [
            {"title": "A", "url": "https://a.example", "content": "snippet a"},
            {"title": "B", "url": "https://b.example", "content": "snippet b"},
        ]
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_FakeHttpResponse(payload))):
        provider = TavilyProvider(api_key="tvly-fake-not-real")
        results = await provider.search("some query", max_results=2)

    assert len(results) == 2
    assert results[0].title == "A"
    assert results[0].url == "https://a.example"
    assert results[1].snippet == "snippet b"


@pytest.mark.asyncio
async def test_tavily_provider_handles_empty_results():
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_FakeHttpResponse({}))):
        provider = TavilyProvider(api_key="tvly-fake-not-real")
        results = await provider.search("query with no hits")

    assert results == []
