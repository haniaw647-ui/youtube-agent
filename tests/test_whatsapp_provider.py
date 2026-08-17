from unittest.mock import AsyncMock, patch

import pytest

from src.providers.whatsapp.whatsapp_api import WhatsAppCloudAPIProvider


class _FakeHttpResponse:
    def __init__(self, json_data: dict):
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


@pytest.mark.asyncio
async def test_send_template_message_builds_correct_request():
    fake_resp = _FakeHttpResponse({"messages": [{"id": "wamid.abc123"}]})
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_resp)) as mock_post:
        provider = WhatsAppCloudAPIProvider(phone_number_id="123456", access_token="fake-token")
        result = await provider.send_template_message(
            to="+15551234567",
            template_name="job_status_update",
            params=["Job A", "Published", "url"],
        )

    assert result["messages"][0]["id"] == "wamid.abc123"

    args, kwargs = mock_post.call_args
    assert args[0] == "https://graph.facebook.com/v21.0/123456/messages"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-token"
    body = kwargs["json"]
    assert body["to"] == "+15551234567"
    assert body["template"]["name"] == "job_status_update"
    params = body["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["Job A", "Published", "url"]
