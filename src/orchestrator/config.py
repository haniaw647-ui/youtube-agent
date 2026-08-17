from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "dev-only-not-secure"
    encryption_key: str = ""

    # Object storage (Cloudflare R2) — platform-level, not tenant BYO, since
    # storage is infra the platform provides regardless of provider choice.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
