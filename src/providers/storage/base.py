from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    async def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Uploads and returns a storage reference (e.g. r2://bucket/key) suitable
        for storing in assets.storage_path — not necessarily a directly fetchable
        URL unless the bucket/domain is public (see R2_PUBLIC_BASE_URL)."""
        raise NotImplementedError
