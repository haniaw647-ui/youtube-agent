from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    async def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Uploads and returns a storage reference (e.g. r2://bucket/key) suitable
        for storing in assets.storage_path — not necessarily a directly fetchable
        URL unless the bucket/domain is public (see R2_PUBLIC_BASE_URL)."""
        raise NotImplementedError

    @abstractmethod
    async def download_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    @staticmethod
    def key_from_storage_path(storage_path: str) -> str:
        """'r2://bucket/some/key' -> 'some/key' — the inverse of what
        upload_bytes's return value encodes, for stages that need to re-fetch
        a previously stored asset."""
        without_scheme = storage_path.removeprefix("r2://")
        _, _, key = without_scheme.partition("/")
        return key
