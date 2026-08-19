import httpx

from src.providers.visual.base import VisualProvider, VisualResult

PIXABAY_URL = "https://pixabay.com/api/"


class PixabayProvider(VisualProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, count: int = 1) -> list[VisualResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                PIXABAY_URL,
                params={
                    "key": self._api_key,
                    "q": query,
                    "image_type": "photo",
                    # Pixabay's API rejects per_page below 3 outright.
                    "per_page": max(count, 3),
                },
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            VisualResult(
                url=hit["largeImageURL"],
                photographer=hit.get("user", ""),
                source="pixabay",
                license_type="pixabay-content-license",
            )
            for hit in data.get("hits", [])[:count]
        ]
