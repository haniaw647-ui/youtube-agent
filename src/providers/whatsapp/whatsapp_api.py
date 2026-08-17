import httpx

from src.providers.whatsapp.base import WhatsAppProvider

GRAPH_API_VERSION = "v21.0"
LANGUAGE_CODE = "en_US"


class WhatsAppCloudAPIProvider(WhatsAppProvider):
    def __init__(self, phone_number_id: str, access_token: str):
        self._phone_number_id = phone_number_id
        self._access_token = access_token

    async def send_template_message(self, to: str, template_name: str, params: list[str]) -> dict:
        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self._phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": LANGUAGE_CODE},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in params],
                    }
                ],
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url, headers={"Authorization": f"Bearer {self._access_token}"}, json=body
            )
        resp.raise_for_status()
        return resp.json()
