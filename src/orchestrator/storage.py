from src.orchestrator.config import get_settings
from src.providers.storage.base import StorageProvider
from src.providers.storage.r2 import R2StorageProvider


def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    return R2StorageProvider(
        account_id=settings.r2_account_id,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket=settings.r2_bucket_name,
    )
