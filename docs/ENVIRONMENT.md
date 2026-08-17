# Environment Variables

Split into two categories now (ARCHITECTURE.md §7):

- **Platform env vars** (this section) — set once in Railway, same for every tenant.
- **Tenant-level API keys** — *not* env vars anymore. They live encrypted in the `tenant_api_keys` table, entered by each tenant through their dashboard settings (DATA_FLOW.md "Stage -0.5"). Do not add a tenant's personal API key to `.env` or Railway config — that would defeat the entire point of BYO keys and mix tenant secrets into platform-shared config.

Local development uses a `.env` file (git-ignored) loaded via `pydantic-settings`, mirroring these same names.

## Core / infra

| Variable | Required | Description | Example |
|---|---|---|---|
| `ENVIRONMENT` | yes | `development` \| `staging` \| `production` | `production` |
| `DATABASE_URL` | yes | Supabase Postgres connection string | `postgresql+asyncpg://...supabase.co:5432/postgres` |
| `SUPABASE_URL` | yes | Supabase project URL (used by the Auth client) | `https://xxxx.supabase.co` |
| `SUPABASE_ANON_KEY` | yes | Public anon key, used client-side for Supabase Auth flows | — |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Server-side key that bypasses RLS — **only ever used server-side for platform-level operations** (e.g. internal ops dashboard queries across tenants); application code paths that act on behalf of a specific tenant must use the tenant's own session context, not this key, or RLS provides no protection | — |
| `REDIS_URL` | yes | Celery broker/result backend (Railway plugin) | `redis://default:pass@host:6379/0` |
| `SECRET_KEY` | yes | App-level signing key (session cookies, CSRF) | random 32+ byte value |
| `ENCRYPTION_KEY` | yes | Fernet key encrypting tenant API keys and OAuth refresh tokens at rest | `Fernet.generate_key()` output |
| `LOG_LEVEL` | no | `DEBUG` \| `INFO` \| `WARNING` | `INFO` |

## Internal ops dashboard auth

| Variable | Required | Description |
|---|---|---|
| `OPS_ADMIN_USERNAME` | yes | Platform operator login — **separate identity system from tenant auth** (ARCHITECTURE.md §7); do not reuse Supabase tenant sessions here |
| `OPS_ADMIN_PASSWORD_HASH` | yes | bcrypt hash |

## Object storage (Cloudflare R2)

| Variable | Required | Description |
|---|---|---|
| `R2_ACCOUNT_ID` | yes | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | yes | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | yes | R2 API token secret |
| `R2_BUCKET_NAME` | yes | Bucket storing all tenants' generated media (tenant-scoped by path prefix, not separate buckets) |

## Platform-shared YouTube OAuth

| Variable | Required | Description |
|---|---|---|
| `YOUTUBE_OAUTH_CLIENT_ID` | yes | The platform's single Google Cloud OAuth client — every tenant authorizes against this same client |
| `YOUTUBE_OAUTH_CLIENT_SECRET` | yes | Client secret |
| `YOUTUBE_OAUTH_REDIRECT_URI` | yes | Callback URL for the per-tenant/per-channel authorization flow |

Per-tenant refresh tokens live encrypted in `channels.youtube_refresh_token_encrypted` (DATA_FLOW.md), never as env vars.

## Platform-shared WhatsApp

| Variable | Required | Description |
|---|---|---|
| `WHATSAPP_PHONE_NUMBER_ID` | yes | Meta Cloud API sending number ID (one number for the whole platform) |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | yes | Meta WABA ID |
| `WHATSAPP_ACCESS_TOKEN` | yes | Permanent system-user access token (encrypted at rest) |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | yes | Verifies the webhook endpoint during Meta setup |
| `WHATSAPP_APP_SECRET` | yes | Verifies `X-Hub-Signature-256` on incoming delivery-status webhooks |

Per-tenant recipient numbers live in `channels.whatsapp_recipient_number` (DATA_FLOW.md), not env vars.

## Platform-provided music library

| Variable | Required | Description |
|---|---|---|
| `MUSIC_LIBRARY_PATH` | no | Not yet read by any code. Phase 4's `background_music` stage generates a synthesized placeholder tone instead (`license_type='platform-placeholder-not-for-production'` on the asset) — sourcing a real curated/licensed set (API_REQUIREMENTS.md §2) is a content decision for the user, not something to fabricate. This var is reserved for when that's wired in. |

## Subtitle timing — resolved without Whisper (Phase 4)

No `WHISPER_MODE` env var exists. The three options this was meant to decide
between (platform-absorbed OpenAI cost, tenant's own key, self-hosted) turned
out to be solving a harder problem than the pipeline actually has: we already
know the exact narration text per scene (it's what we synthesized speech
from), so there's nothing to *transcribe* — only timing to estimate. Subtitle
cue timing is computed from each scene's word count scaled against the real
(ffprobe-measured) narration audio duration — see
`src/workers/stages/video_assembly.py` and `_srt.py`. No API call, no cost,
no extra dependency. Revisit only if word-count-proportional timing proves
visibly wrong against real speech (e.g. TTS providers with very uneven
pacing) — real Whisper alignment remains a valid upgrade path if so.

## Cost/safety guardrails

| Variable | Required | Description |
|---|---|---|
| `MAX_JOBS_PER_TENANT_PER_DAY` | no | Prevents one tenant (bug or abuse) from starving shared YouTube quota (ARCHITECTURE.md §9) |
| `MAX_CONCURRENT_JOBS_PER_TENANT` | no | Queue-fairness cap (ARCHITECTURE.md §8) |
| `YOUTUBE_DAILY_QUOTA_ALERT_THRESHOLD` | no | Ops-dashboard alert when platform-wide YouTube quota usage crosses this fraction of the daily cap |

## What's explicitly NOT here anymore (moved to per-tenant DB rows)

`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `TAVILY_API_KEY`/`SERPER_API_KEY`, `PEXELS_API_KEY`/`PIXABAY_API_KEY`, `REPLICATE_API_TOKEN`, `RUNWAY_API_KEY`, `CANVA_API_KEY`, and any other provider-specific key that a tenant supplies themselves. If you find yourself adding one of these to Railway env vars during implementation, stop — it almost certainly belongs in `tenant_api_keys` instead (the one narrow exception is `OPENAI_API_KEY` for platform-run Whisper mode, above, and even that's a deliberate, revisitable call, not a default).
