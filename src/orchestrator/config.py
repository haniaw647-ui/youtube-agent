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

    # The app sits behind Railway's reverse proxy, so request.base_url can
    # resolve to the proxy's internal address rather than the public HTTPS
    # domain — exactly the kind of thing that silently breaks any URL we
    # build from it (e.g. the password-reset redirect_to). Explicit and
    # unambiguous, same reasoning as YOUTUBE_OAUTH_REDIRECT_URI below.
    public_base_url: str = ""

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

    # Phase 9 cost/safety guardrails (ARCHITECTURE.md §8) — protect a shared
    # worker fleet and the shared YouTube quota from one tenant's runaway usage.
    max_jobs_per_tenant_per_day: int = 5
    max_concurrent_jobs_per_tenant: int = 2
    youtube_quota_alert_threshold: float = 0.8

    # Internal ops dashboard (Phase 9) — a single shared operator credential,
    # not a per-tenant Supabase Auth account. Deliberately avoids needing
    # SUPABASE_SERVICE_ROLE_KEY: cross-tenant reads already go through
    # service_session() (direct Postgres role, bypasses RLS) — the only new
    # thing this needs is a way to gate who can reach those routes at all.
    admin_dashboard_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
