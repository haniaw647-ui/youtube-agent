from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.storage.r2 import R2StorageProvider
from src.providers.visual.pexels import PexelsProvider
from src.providers.voice.elevenlabs import ElevenLabsProvider
from src.providers.voice.openai_tts import OpenAITTSProvider


class _FakeHttpResponse:
    def __init__(self, content: bytes = b"", json_data: dict | None = None):
        self.content = content
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


@pytest.mark.asyncio
async def test_elevenlabs_provider_returns_audio_bytes():
    fake_resp = _FakeHttpResponse(content=b"fake-mp3-bytes")
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_resp)) as mock_post:
        provider = ElevenLabsProvider(api_key="fake-key")
        result = await provider.synthesize("hello world")

    assert result == b"fake-mp3-bytes"
    _, kwargs = mock_post.await_args
    assert kwargs["headers"]["xi-api-key"] == "fake-key"


@pytest.mark.asyncio
async def test_openai_tts_provider_returns_audio_bytes():
    fake_resp = _FakeHttpResponse(content=b"fake-openai-audio")
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_resp)) as mock_post:
        provider = OpenAITTSProvider(api_key="fake-key")
        result = await provider.synthesize("hello world")

    assert result == b"fake-openai-audio"
    _, kwargs = mock_post.await_args
    assert kwargs["headers"]["Authorization"] == "Bearer fake-key"


@pytest.mark.asyncio
async def test_pexels_provider_parses_results():
    payload = {
        "photos": [
            {"src": {"large": "https://img.example/1.jpg"}, "photographer": "Jane Doe"},
        ]
    }
    fake_resp = _FakeHttpResponse(json_data=payload)
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_resp)):
        provider = PexelsProvider(api_key="fake-key")
        results = await provider.search("mountains", count=1)

    assert len(results) == 1
    assert results[0].url == "https://img.example/1.jpg"
    assert results[0].source == "pexels"
    assert results[0].license_type == "pexels-free-commercial-use"


@pytest.mark.asyncio
async def test_r2_storage_uploads_and_returns_reference():
    put_object = AsyncMock()

    class _FakeS3Client:
        async def put_object(self, **kwargs):
            return await put_object(**kwargs)

    @asynccontextmanager
    async def _fake_client(*args, **kwargs):
        yield _FakeS3Client()

    provider = R2StorageProvider(
        account_id="acct123",
        access_key_id="key",
        secret_access_key="secret",
        bucket="my-bucket",
    )
    with patch.object(provider._session, "client", new=_fake_client):
        result = await provider.upload_bytes("path/to/file.mp3", b"data", "audio/mpeg")

    assert result == "r2://my-bucket/path/to/file.mp3"
    put_object.assert_awaited_once()
    _, kwargs = put_object.await_args
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "path/to/file.mp3"
    assert kwargs["Body"] == b"data"
