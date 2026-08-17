import httpx

from src.providers.search.base import SearchProvider, SearchResult

TAVILY_URL = "https://api.tavily.com/search"


class TavilyProvider(SearchProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TAVILY_URL,
                json={"api_key": self._api_key, "query": query, "max_results": max_results},
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")
            )
            for r in data.get("results", [])
        ]
