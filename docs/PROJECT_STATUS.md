# Project Status

Living document — update this in the same commit/session as any implementation change. Don't let it drift.

## Where things stand

**Phase 0 (scaffolding) in progress.** Design docs are complete; local scaffolding and cloud infra provisioning are done. Blocked on one thing: a GitHub repo to deploy the `api` service to Railway (see "Immediate next steps").

Done so far:
- Repo layout scaffolded (`src/orchestrator`, `src/workers`, `src/providers/*`, `src/models`, `src/dashboard_tenant`, `src/dashboard_ops`, `tests/`)
- FastAPI skeleton with `/health` endpoint — test passing locally
- Celery app configured (`light`/`heavy` queue routing) against Redis
- Docker Compose (api + worker-light + worker-heavy + redis) for local dev
- Alembic initialized (async, SQLAlchemy 2.0 `Base`), empty initial migration generated and verified
- Ruff + mypy both clean, pytest passing
- CI workflow (`.github/workflows/ci.yml`): lint + type check + test on push — will activate once pushed to GitHub
- **Supabase project created**: `youtube-automation-platform` (ref `yfmgffojwqhodmvvwqsy`, region `us-east-1`) — free tier
- **Railway project created**: `youtube-automation-platform`, with a `redis` service (image `redis:7-alpine`) already running

Not yet done: `api`/`worker-light`/`worker-heavy` services on Railway (waiting on a GitHub repo — Railway's deploy tool requires one), `.env` filled with real Supabase DB password + generated secrets (only `.env.example` exists, matching what's in [ENVIRONMENT.md](ENVIRONMENT.md)).

Historical: all six planning docs below reflect the current design (a multi-tenant product for external YouTubers, not a single-operator personal tool).

- [x] [ARCHITECTURE.md](ARCHITECTURE.md)
- [x] [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- [x] [API_REQUIREMENTS.md](API_REQUIREMENTS.md)
- [x] [ENVIRONMENT.md](ENVIRONMENT.md)
- [x] [DATA_FLOW.md](DATA_FLOW.md)
- [x] PROJECT_STATUS.md (this file)

## Decision history

| Date | Decision | Choice | Where reflected |
|---|---|---|---|
| 2026-08-17 | Language/runtime | Python | ARCHITECTURE.md §3 |
| 2026-08-17 | Deployment (compute) | Railway | ARCHITECTURE.md §10 |
| 2026-08-17 | AI provider tier strategy | Mixed — premium where it most affects quality | API_REQUIREMENTS.md |
| 2026-08-17 | WhatsApp integration | Official Meta WhatsApp Business Cloud API | API_REQUIREMENTS.md §1 |
| 2026-08-17 | **Product scope (pivot)** | **Multi-tenant** — other YouTubers use this as a product, not a personal tool | ARCHITECTURE.md §1 |
| 2026-08-17 | AI provider cost model | **Bring-your-own (BYO) API keys** per tenant | ARCHITECTURE.md §7, API_REQUIREMENTS.md §2 |
| 2026-08-17 | Tenant auth | Managed auth provider — **Supabase Auth** picked as the default (with RLS for isolation) | ARCHITECTURE.md §2, §3 |

## Decisions still open (deliberately deferred)

- **Tenant dashboard tech**: server-rendered (HTMX) vs. thin React frontend — decide during Phase 2, once there's real tenant-facing UI beyond auth/settings (IMPLEMENTATION_PLAN.md Phase 2).
- **Video assembly ceiling**: ffmpeg templates vs. Remotion — revisit only if templates prove visually insufficient (Phase 4).
- **Scheduling mechanism**: Celery Beat vs. Railway native scheduling — decide Phase 1.
- **Whisper mode**: platform-absorbed cost vs. tenant's own key vs. self-hosted — decide Phase 4 once volume is known.
- **Monetization** (Phase 11, explicitly not scheduled): whether/how the platform charges tenants for access itself. BYO keys means this isn't blocking — it's a pricing decision to make once there's a working product.

## Immediate next steps

1. **You, starting now, in parallel with Phase 0**: begin the Google Cloud OAuth consent screen → external/production verification process. This is now the **single longest external lead-time item in the whole project** and directly blocks onboarding any real (non-test) tenant — see ARCHITECTURE.md §9 and IMPLEMENTATION_PLAN.md Phase 0/6. A placeholder privacy policy page is enough to start the process.
2. **You**: begin Meta Business Manager + WhatsApp Business Cloud API setup (shorter lead time than Google's, but still worth starting early — API_REQUIREMENTS.md §1).
3. **You**: create a Supabase account/project when Phase 0 starts (or let me do it if you'd rather hand over credentials/access).
4. **Me** (once you confirm this rewrite matches your intent): start Phase 0 — scaffolding + Supabase/Railway setup.
5. **Legal, before real tenants onboard, not before Phase 0**: Terms of Service, Privacy Policy, Acceptable Use Policy (ARCHITECTURE.md §13) — flagged as a parallel task for you, not something I build.

## Known risks to keep in view

- **Shared YouTube quota is the platform's real scaling ceiling** (ARCHITECTURE.md §9): ~6 uploads/day across ALL tenants combined on Google's default quota. Requesting an increase needs to happen proactively as tenant count grows, not reactively after uploads start failing.
- **Cross-tenant data isolation (RLS) must be proven with automated tests from Phase 1 onward**, not just designed — this is the single highest-consequence thing to get right early (IMPLEMENTATION_PLAN.md Phase 1).
- **Abuse/content-policy surface is new** with multi-tenancy — a single operator only had to trust themselves; now the platform is a pipe for other people's content choices straight to YouTube (ARCHITECTURE.md §14).
- Copyright/licensing on generated visuals/music is now partly the *tenant's* responsibility (their own provider accounts) but the platform still needs to make license status visible/auditable per asset, not silently assume it's fine (ARCHITECTURE.md §11).

## Changelog

- **2026-08-17**: Initial single-operator architecture and supporting docs written.
- **2026-08-17**: Pivoted to multi-tenant product design (other YouTubers as customers), BYO AI provider keys, Supabase Auth+RLS for tenant isolation. All six docs rewritten to reflect this. No code yet.
- **2026-08-17**: Phase 0 started — repo scaffolding, FastAPI/Celery/Alembic skeleton, Docker Compose, CI workflow all in place and verified locally (tests/lint/type-check passing). Supabase project and Railway project (+ Redis service) provisioned. Blocked on a GitHub repo to deploy the `api` service.
