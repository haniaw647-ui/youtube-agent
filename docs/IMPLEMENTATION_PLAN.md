# Implementation Plan

Phased build order for the multi-tenant platform. Two changes from a single-operator plan matter most: (1) tenant isolation (Supabase Auth + RLS) has to be foundational — it's much harder to retrofit onto tables that already have data — so it moves into Phase 1, not a later "add auth" phase; (2) Google OAuth app verification (ARCHITECTURE.md §9) has the longest external lead time of anything in this project and directly blocks onboarding real tenants, so it starts immediately, in parallel with Phase 0.

**Sequencing principle, unchanged**: prove the cheap text pipeline first, entirely mocked-media, before spending on any TTS/visual/video work. Now doubly true since BYO keys means *tenants'* money is what's at stake in getting this wrong, not just yours.

---

## Phase 0 — Scaffolding + start the slow external clock

**Goal**: repo structure, local dev loop, empty deploy pipeline, and the Google OAuth verification process *started* (even though it's not needed until Phase 6).

- Python project layout (`src/orchestrator`, `src/workers`, `src/providers/{voice,visual,music,thumbnail,search,llm}`, `src/models`, `src/dashboard_tenant`, `src/dashboard_ops`, `tests/`)
- Docker Compose: Redis + app (Supabase has its own local dev CLI for Postgres+Auth, run alongside)
- FastAPI app skeleton, health check endpoint
- Supabase project created; Alembic initialized against it, first migration (empty)
- Railway project created, Redis plugin attached, `api` service deployed from skeleton
- CI: lint (ruff) + type check + test run on push
- **User action, started in parallel, not blocking this phase**: create the Google Cloud project, enable YouTube Data/Analytics API, begin the OAuth consent screen → external/production verification process (needs a privacy policy URL — a placeholder page is enough to start the process, replace before going live)

**Demo**: `GET /health` returns 200 on Railway. Google verification request is submitted (even if still pending).

## Phase 1 — Tenant auth, isolation, and orchestrator core

**Goal**: the multi-tenant foundation is real and *tested*, not assumed — this is the phase where getting it wrong is most expensive to unwind later.

- Supabase Auth wired: tenant signup/login/session
- Full schema from [DATA_FLOW.md](DATA_FLOW.md): `tenants`, `tenant_api_keys`, `channels`, `jobs`, `job_stages`, `topics`, `scripts`, `assets`, `api_call_logs`, `approvals`, `youtube_videos`, `analytics_snapshots`, `notifications_sent`
- **RLS policy on every table, with an actual isolation test**: create two tenants, confirm tenant A's session cannot read/write tenant B's rows under any code path, including the API layer — write this as a real automated test, not a manual spot-check, and keep it in CI so a future migration can't silently regress it
- Separate internal ops auth (§7 ARCHITECTURE.md) — distinct login, service-role DB access, clearly separated from tenant sessions
- Job state machine: stage sequence, transitions, retry policy, approval-gate pause/resume — same mechanics as a single-tenant design, now tenant/channel-scoped
- Celery wired to Redis, `light`/`heavy` queue routing
- Each pipeline stage exists as a stub task (fake artifact, marks done) — proves sequencing/parallelism/retry before real providers are involved
- Tenant API key vault: connect/store/validate-at-save-time UI (minimal) for `tenant_api_keys`
- Channel config CRUD, scoped to the authenticated tenant

**Demo**: two test tenant accounts, each creates a channel and a job, each walks through all 15 stub stages independently; automated test proves tenant A cannot see tenant B's jobs/topics/keys via the API.

## Phase 2 — Real text pipeline (topic → research → script → QA)

**Goal**: Stages 1–5 produce genuinely good output, using each tenant's own connected keys.

- `providers/llm/anthropic.py` adapter, reads the calling tenant's own key from `tenant_api_keys`
- Topic generation with scoring formula, duplicate prevention
- `providers/search/{tavily,serper}.py` adapters
- Script writing with scene segmentation
- Script QA pass with revision loop + baseline content-policy check (ARCHITECTURE.md §14)
- Approval-gate UI for topic/script in the (still minimal) tenant dashboard
- **Decide dashboard tech now** (server-rendered vs. thin React) — deferred question from ARCHITECTURE.md §3, resolve it here since Phase 2 is the first phase with real tenant-facing UI beyond auth/settings

**Demo**: a real test tenant, using their own Anthropic key, gets scored topic candidates and a QA-passed script through the dashboard. **Stop and review script quality with a couple of real prospective users here** — cheapest point to tune prompts/style before building anything expensive on top.

## Phase 3 — Voice-over & visuals

**Goal**: Stages 6a/6b, using tenant-connected keys, in parallel.

- `providers/voice/elevenlabs.py` + a second adapter (OpenAI TTS) — proves the BYO-provider interface is genuinely swappable per tenant, not just in theory
- `providers/visual/pexels.py` as the default (free, zero licensing ambiguity, pre-selected for new tenants per API_REQUIREMENTS.md §2); generative-image/video adapters built only when a real tenant needs them
- R2 storage wiring, tenant/channel-scoped paths, `assets.license_type` populated on every visual

**Demo**: a QA-passed script produces narration + full visual set for a real test tenant, cost attributed to their own provider account (visible in their dashboard's cost log, not a platform bill).

## Phase 4 — Video assembly, subtitles, music

**Goal**: Stages 7–9 — a real finished video file.

- ffmpeg-based assembly: timed visual sequencing, basic pan/zoom template
- Whisper-based subtitle alignment — **decide `WHISPER_MODE` here** (platform-absorbed vs. tenant's own key vs. self-hosted, ENVIRONMENT.md)
- Subtitle burn-in
- Platform-provided curated/licensed music library (API_REQUIREMENTS.md §2) with ducking; attribution captured on the asset

**Demo**: a fully assembled MP4 with captions and music, downloadable from the tenant dashboard.

## Phase 5 — Thumbnail & metadata

**Goal**: Stages 10–11.

- Thumbnail: generative image + Pillow overlay; prototype Canva Connect as an alternative (connector already available in this environment)
- Metadata generation: title candidates, description (with attributions), tags

**Demo**: a job produces a complete "ready to review" bundle for a real test tenant.

## Phase 6 — Final QA, human checkpoint, and YouTube connect goes live

**Goal**: Stage 12 + the default approval gate + the platform's YouTube OAuth app is verified and ready for real tenants (this is why Phase 0's parallel clock matters — check verification status now).

- Automated checklist: sync check, resolution/aspect ratio, metadata limits, **license audit** (blocks auto-approval on unresolved licenses), content-policy recheck
- Tenant dashboard approval queue UI: review inline, approve/reject/request-changes
- Gate defaults **on** for every new channel (tenant-overridable)
- **If Google OAuth verification (started Phase 0) isn't done yet, this phase blocks on it** — don't proceed to Phase 7 with only test-mode OAuth if the goal is onboarding real external tenants; a handful of manually-added test users is fine for continued internal development, but not for a real launch

**Demo**: a finished job lands in a real test tenant's approval queue; they review and approve it themselves.

## Phase 7 — YouTube publishing

**Goal**: Stage 13, real uploads, tenant-connects-their-own-channel flow live.

- Per-tenant YouTube channel connect flow (OAuth against the platform's shared, now-verified client), refresh token stored encrypted per channel
- Upload with metadata + thumbnail; scheduled-publish support
- **Platform-wide quota tracking on the ops dashboard** (shared 10,000 units/day ceiling, ARCHITECTURE.md §9) — build this alongside the upload feature, not after, since quota exhaustion silently blocking every tenant's uploads is the single worst failure mode to discover late

**Demo**: a real test tenant connects their own YouTube channel and an approved job publishes to it.

**Run this against 2-3 real trusted test tenants first**, not a public launch — confirms the full multi-tenant path (their keys, their channel, their approval) before opening signups.

## Phase 8 — WhatsApp notifications

**Goal**: Stage 14, platform-shared sending number, per-tenant recipient.

- Meta Business Manager + Developer App + WhatsApp product setup, phone number, message template submission (start during Phase 4/5 — real but shorter lead time than Google's)
- Webhook endpoint for delivery status, signature-verified
- Per-channel recipient number config in tenant settings
- Notification on job completion (success with video URL, or terminal failure with reason)

**Demo**: a test tenant's completed/failed job sends a real WhatsApp message to *their own* configured number.

## Phase 9 — Analytics & the internal ops dashboard

**Goal**: Stage 15, and the ops dashboard becomes a real operating surface for you as platform operator.

- Celery Beat scheduled analytics pulls (+1/+7/+30 day snapshots), per tenant channel
- Tenant dashboard: their own analytics charts
- **Internal ops dashboard** (separate from tenant-facing): all-tenant job monitor, error/failure list, **platform-wide YouTube quota usage** (critical, §9), tenant key-connection health (status only, never decrypted values), abuse signals (ARCHITECTURE.md §14)
- Cost/safety guardrails wired in: `MAX_JOBS_PER_TENANT_PER_DAY`, `MAX_CONCURRENT_JOBS_PER_TENANT`, quota alert threshold

**Demo**: as the operator, you can see system-wide health and every tenant's status without touching their data directly; a tenant can see only their own analytics.

## Phase 10 — Scheduling & hardening for real multi-tenant operation

**Goal**: the platform runs unattended per-channel, safely, across many tenants at once.

- Celery Beat (or Railway scheduled deploys) triggers topic generation on each channel's cadence
- Per-tenant auto-approve rules for channels configured for full autonomy
- Per-tenant concurrency caps enforced (queue fairness, ARCHITECTURE.md §8)
- Load/cost testing against a realistic multi-tenant schedule — confirm shared YouTube quota holds, confirm RLS isolation holds under concurrent load, not just single-request tests
- Runbook: "tenant's job stuck," "worker crashed," "platform YouTube quota exceeded" (now a platform-wide incident, not one channel's problem), "WhatsApp template rejected," "a tenant's own API key expired mid-job"

**Demo**: several real tenants running unattended for a full posting cycle each, verified against their own configured approval gates, with no cross-tenant incidents.

## Phase 11 (not scheduled — business decision, not engineering) — Monetization

Stripe billing for platform access itself (ARCHITECTURE.md §12), if/when you decide on pricing. Deliberately not part of the build sequence above — add it once there's a working product worth charging for.

---

## What triggers moving to the next phase

Same principle as before: each phase's "Demo" line is the exit criterion against real (not mocked) dependencies. Three phases now have hard stop-and-check points: **Phase 1** (isolation must be *proven*, not assumed, before any real tenant data exists), **Phase 2** (script quality, cheap to fix now, compounds expensively later), and **Phase 6/7** (the Google OAuth verification dependency literally blocks progress if it isn't done — check its status at the start of Phase 0 already, don't wait until Phase 6 to find out it's still pending).
