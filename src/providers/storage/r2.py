from contextlib import asynccontextmanager

import aioboto3

from src.providers.storage.base import StorageProvider


class R2StorageProvider(StorageProvider):
    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str):
        self._endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._session = aioboto3.Session()

    @asynccontextmanager
    async def _client(self):
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name="auto",
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
