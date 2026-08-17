import httpx

from src.providers.visual.base import VisualProvider, VisualResult

PEXELS_URL = "https://api.pexels.com/v1/search"


class PexelsProvider(VisualProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, count: int = 1) -> list[VisualResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                PEXELS_URL,
                headers={"Authorization": self._api_key},
                params={"query": query, "per_page": count},
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            VisualResult(
                url=photo["src"]["large"],
                photographer=photo.get("photographer", ""),
                source="pexels",
                license_type="pexels-free-commercial-use",
            )
            for photo in data.get("photos", [])
        ]
