# AutoTube — Multi-Tenant YouTube Automation Platform

An end-to-end platform that takes a YouTube channel from "idea" to "published video" with minimal human involvement: topic generation → research → script → voice-over → visuals → video assembly → subtitles → music → thumbnail → metadata → quality check → YouTube upload → WhatsApp notification.

It's built as a **multi-tenant SaaS product** — other YouTubers sign up and run their own channels through it, bringing their own AI provider API keys (Anthropic, ElevenLabs, Pexels, etc.). The platform itself never bills for AI usage; it charges (eventually) for access to the pipeline.

## What's here

- **Tenant dashboard** (`/dashboard`) — signup, channel management, starting/monitoring jobs, per-video analytics, approval queue, BYO API key vault. Self-serve, no raw API calls needed.
- **Internal ops dashboard** (`/admin`) — cross-tenant job monitor, failure list, shared YouTube quota usage, tenant key-connection health, content-policy abuse signals. Operator-only, single shared password.
- **15-stage Celery pipeline**, fully real (no stubs): every stage from topic generation through YouTube upload and WhatsApp notification actually does the work, backed by swappable provider adapters.
- **Row-Level Security** at the Postgres level for real tenant data isolation, not just application-logic checks — proven with automated tests that run real concurrent cross-tenant requests against production.
- **Scheduling**: channels can auto-generate content on a cadence (daily/weekly/etc.) via Celery Beat, with per-tenant safety guardrails (daily job caps, concurrency limits) so no single tenant can starve the shared worker fleet or YouTube quota.

## Tech stack

Python 3.12 · FastAPI · SQLAlchemy (async) + asyncpg · Celery + Redis · Supabase (Postgres + Auth) · Jinja2 server-rendered dashboards · Railway (hosting) · Cloudflare R2 (object storage, planned) · ffmpeg (video assembly) · Pillow (thumbnails)

## Documentation

Start with **[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)** — the living source of truth for what's built, what's deployed, what's blocked on credentials, and the full changelog. The rest of `docs/`:

| Doc | What it covers |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, multi-tenancy model, provider-swappable architecture |
| [`DATA_FLOW.md`](docs/DATA_FLOW.md) | How a job moves through the 15 pipeline stages |
| [`API_REQUIREMENTS.md`](docs/API_REQUIREMENTS.md) | External services needed (AI providers, YouTube, WhatsApp) and why |
| [`ENVIRONMENT.md`](docs/ENVIRONMENT.md) | Full environment variable reference |
| [`IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | The phased build plan (Phases 0–11) |
| [`RUNBOOK.md`](docs/RUNBOOK.md) | Operator playbook: stuck jobs, worker crashes, quota exceeded, notification failures, expired tenant keys |

## Running locally

```bash
cp .env.example .env   # fill in real values — see docs/ENVIRONMENT.md
docker compose up
```

This starts the API (`:8000`), a light-queue worker, a heavy-queue worker (ffmpeg/render-bound stages), and Redis. The API and workers connect to your configured Supabase Postgres instance, not a local database — there's no local Postgres in `docker-compose.yml` by design, since RLS and the Supabase Auth integration need the real thing.

Without `docker compose`, running directly:

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
alembic upgrade head
uvicorn src.orchestrator.main:app --reload
# separately:
celery -A src.workers.celery_app worker -Q light --concurrency=4
celery -A src.workers.celery_app worker -Q heavy --concurrency=2
```

## Testing

```bash
ruff check .
mypy src/
pytest
```

Most tests run against a **real** Supabase Postgres instance (not mocks) — connection isolation, RLS enforcement, and quota-concurrency behavior are all proven with genuine concurrent requests, not simulated. You'll need `DATABASE_URL` and friends set in `.env` (see `docs/ENVIRONMENT.md`) for the suite to pass. One test (`test_ffmpeg_pipeline.py`) needs `ffmpeg` on `PATH`.

## Deployment

Hosted on [Railway](https://railway.com): `api`, `worker-light`, `worker-heavy`, and `redis` as separate services, all deploying from this repo's `main` branch. CI (`.github/workflows/ci.yml`) runs lint, type-check, and the full test suite (including the real-Postgres tests) on every push.

**If `DATABASE_URL` is ever regenerated** from the Supabase dashboard, grab the **Session pooler** connection string, not the default "direct connection" one — the direct connection is IPv6-only on this project's tier and silently breaks every deployed service (see `docs/PROJECT_STATUS.md`'s Phase 9 entry for the full story).
