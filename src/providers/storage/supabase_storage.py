from contextlib import asynccontextmanager

import aioboto3

from src.providers.storage.base import StorageProvider


class SupabaseStorageProvider(StorageProvider):
    """Supabase Storage's S3-compatible endpoint — same shape as R2StorageProvider,
    kept as its own class rather than a shared abstraction to match this
    codebase's existing one-file-per-provider convention (see the voice/visual
    providers). storage_path URIs still use the r2:// scheme regardless of
    which provider actually wrote them — it's just an internal marker
    (StorageProvider.key_from_storage_path), not tied to the literal service."""

    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str,
                 bucket: str, region: str):
        # Deliberately not derived from SUPABASE_URL — Supabase's S3-compatible
        # endpoint lives on a different subdomain (storage.supabase.co, not
        # supabase.co), confirmed against the real value shown in the
        # dashboard rather than guessed, so it's taken as its own explicit
        # setting instead.
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()

    @asynccontextmanager
    async def _client(self):
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name=self._region,
        ) as s3:
            yield s3

    async def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return f"r2://{self._bucket}/{key}"

    async def download_bytes(self, key: str) -> bytes:
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()
