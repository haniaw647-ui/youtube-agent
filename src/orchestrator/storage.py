from src.orchestrator.config import get_settings
from src.providers.storage.base import StorageProvider
from src.providers.storage.r2 import R2StorageProvider
from src.providers.storage.supabase_storage import SupabaseStorageProvider


def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    if settings.supabase_storage_access_key_id:
        return SupabaseStorageProvider(
            endpoint_url=settings.supabase_storage_endpoint,
            access_key_id=settings.supabase_storage_access_key_id,
            secret_access_key=settings.supabase_storage_secret_access_key,
            bucket=settings.supabase_storage_bucket,
            region=settings.supabase_storage_region,
        )
    return R2StorageProvider(
        account_id=settings.r2_account_id,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket=settings.r2_bucket_name,
    )
