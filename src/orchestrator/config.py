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

    # Shared platform-level YouTube OAuth client — every tenant authorizes
    # this same client against their own channel (ARCHITECTURE.md §9).
    youtube_oauth_client_id: str = ""
    youtube_oauth_client_secret: str = ""
    youtube_oauth_redirect_uri: str = ""

    # Shared platform-level WhatsApp Business Cloud API — one sending number,
    # per-tenant recipient (ARCHITECTURE.md §9's "why these stay platform-level").
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_webhook_verify_token: str = ""
    whatsapp_app_secret: str = ""
    # Meta requires business-initiated messages to use a pre-approved template.
    # See docs/PROJECT_STATUS.md for the exact body text submitted for approval.
    whatsapp_template_name: str = "job_status_update"


@lru_cache
def get_settings() -> Settings:
    return Settings()
