import httpx


async def download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return resp.content
