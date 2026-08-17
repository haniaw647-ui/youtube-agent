# Architecture — YouTube Content Automation Platform

Status: **Design phase — nothing implemented yet.** This document is the source of truth for how the system is built. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for what's actually done so far.

## 0. Starting point and pivot history

This is a **greenfield build** — `youtube agent/` was empty when this design work began. There is no existing framework, database, or codebase to extend.

Decisions locked in with the user, in order:

| Date | Decision | Choice |
|---|---|---|
| 2026-08-17 | Language/runtime | Python |
| 2026-08-17 | Deployment target (compute) | Cloud — Railway |
| 2026-08-17 | AI provider quality tier | Mixed: premium where it most affects output quality, budget elsewhere |
| 2026-08-17 | WhatsApp channel | Official Meta WhatsApp Business Cloud API |
| 2026-08-17 | **Product scope (pivot)** | **Multi-tenant product** — other YouTubers sign up and use this for their own channels, not a personal tool for one operator |
| 2026-08-17 | AI provider cost model | **Bring-your-own (BYO) API keys** — each tenant supplies their own provider keys; the platform never holds provider billing risk |
| 2026-08-17 | Tenant auth | Managed auth provider (this doc picks **Supabase Auth** as the default — see §2) |

**This version supersedes the single-operator design.** The core pipeline (topic → research → script → voice → visuals → assembly → thumbnail → QA → upload → notify → analytics) is unchanged — see [DATA_FLOW.md](DATA_FLOW.md). What changed is everything *around* it: every table now scopes to a tenant, auth is real (not a single admin password), API keys live per-tenant instead of in platform env vars, and there are two dashboards instead of one.

## 1. What "multi-tenant" actually means here

- A **tenant** = one YouTuber/customer account. A tenant can connect **multiple YouTube channels** (many creators run more than one).
- Tenants **never see each other's data** — topics, scripts, jobs, assets, API keys, analytics. This is enforced at the database level (Postgres Row-Level Security), not just in application code, because application-level-only isolation is one bug away from a cross-tenant data leak — unacceptable when tenants are storing other people's YouTube OAuth tokens and their own paid API keys with us.
- Each tenant brings their **own API keys** for Anthropic, ElevenLabs, Pexels, etc. (§7). We orchestrate; we don't bill for or intermediate their AI provider spend. This removes the need for usage-metering/billing infrastructure in v1 — a deliberate scope cut to ship faster (see §11).
- **YouTube upload and WhatsApp sending stay platform-level, not per-tenant**, for practical reasons explained in §7 and §9 — this is the one place "BYO" doesn't apply, and it's also the platform's biggest scaling constraint (§9).

## 2. High-level module map

```
┌─────────────────────────────┐   ┌─────────────────────────────────┐
│   Tenant Dashboard (Web)      │   │   Internal Ops Dashboard (Web)   │
│ their channels · their jobs · │   │ all tenants · system health ·    │
│ approvals · their API keys    │   │ quota usage · support · abuse    │
└───────────────┬───────────────┘   └─────────────────┬─────────────────┘
                 │                                      │
                 │        REST/HTTP (FastAPI, tenant-scoped by session)
┌────────────────▼──────────────────────────────────────▼─────────────────┐
│                              Orchestrator API                             │
│   job/state machine · scheduling · retry policy · approval gates ·       │
│   tenant/channel management · per-tenant API key vault                    │
└───────────────────────────────┬───────────────────────────────────────────┘
                                 │ enqueue (Celery + Redis)
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                          ▼
┌───────────────┐      ┌──────────────────┐        ┌──────────────────┐
│  Text Workers   │      │  Media Workers    │        │  Publish Workers  │
│ topic/research/ │      │ voice/visuals/    │        │ YouTube upload/   │
│ script/QA       │      │ render/thumbnail  │        │ WhatsApp notify   │
│ (uses tenant's   │      │ (uses tenant's    │        │ (shared platform   │
│  own API keys)   │      │  own API keys)    │        │  OAuth app + WA #) │
└───────┬─────────┘      └────────┬─────────┘        └────────┬─────────┘
        │                         │                             │
        └─────────────┬───────────┴──────────────┬──────────────┘
                       ▼                          ▼
        ┌───────────────────────────┐   ┌──────────────────┐
        │  Supabase (Postgres + Auth) │   │  Object Storage    │
        │  RLS-enforced, every table   │   │ (Cloudflare R2)    │
        │  scoped by tenant_id         │   │ tenant/channel/job  │
        └───────────────────────────┘   └──────────────────┘
```

Compute (API + workers) runs on **Railway**. Data and auth run on **Supabase** — a deliberate split: Railway is a good fit for long-running Celery workers and ffmpeg containers; Supabase gives us Postgres + Auth + Row-Level Security as one integrated piece, which is the single highest-leverage decision for building multi-tenant isolation correctly and quickly. The app connects to the Supabase Postgres instance the same way it would connect to any Postgres (`DATABASE_URL`) — this isn't a lock-in to Supabase's client libraries, just their managed Postgres + Auth service.

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | User preference; strong ecosystem for ffmpeg/video/audio tooling. |
| API framework | FastAPI | Async-native, typed, auto-generates OpenAPI. |
| Tenant dashboard | FastAPI + Jinja2 + HTMX + Alpine.js, or a thin React frontend against the same API | Revisit once the tenant-facing UI needs matter (file upload previews, drag-drop approval, richer real-time job status) — a customer-facing product UI has a lower bar for "server-rendered is enough" than an internal tool. Decide during Phase 2 (IMPLEMENTATION_PLAN.md). |
| Internal ops dashboard | FastAPI + Jinja2 + HTMX (kept deliberately simple — internal tool, not a product surface) | |
| Auth + tenant DB | **Supabase** (Postgres + Auth + Row-Level Security) | Auth (signup/login/session/password-reset/email-verification) out of the box, and RLS policies enforce tenant isolation at the database layer — the right foundation for a product holding other people's OAuth tokens and API keys. Alternative considered: Clerk (auth only) + plain Railway Postgres with app-level tenant checks — rejected because RLS is a materially stronger isolation guarantee for this specific risk profile. |
| Task queue | Celery + Redis (Railway) | Best-supported chaining/retry primitives in Python (`chain`, `group`, `chord`) — maps directly onto a multi-stage pipeline with parallel stages. |
| ORM/migrations | SQLAlchemy 2.0 + Alembic | Explicit migrations; RLS policies are managed as SQL alongside schema migrations, not bolted on separately. |
| Object storage | Cloudflare R2 (S3-compatible) | No egress fees — matters since finished videos are 100MB–1GB+ each, times many tenants. |
| Video assembly | ffmpeg, driven via `ffmpeg-python` | Scriptable, runs anywhere Railway can run a container. |
| Local dev | Docker Compose (Redis + app; Supabase has a local dev CLI/emulator for Postgres+Auth) | Mirrors prod topology. |

## 4. The pipeline as a state machine

Unchanged from the single-tenant design in mechanics — every video is still a `Job` moving through fixed stages — except every `Job` now belongs to a `channel`, which belongs to a `tenant`, and every provider call in the text/media stages uses **that tenant's own API key**, not a platform-wide one.

```
topic_generation → topic_scoring → [approval?] → research → script_writing
   → script_qa → [approval?] → voice_over → visual_generation (parallel w/ voice_over)
   → video_assembly → subtitle_burn_in → background_music → thumbnail_generation
   → metadata_generation (title/description/tags) → final_qa → [approval?]
   → youtube_upload → whatsapp_notification → analytics_tracking (ongoing)
```

Full stage-by-stage data flow (inputs/outputs/storage/DB) is in [DATA_FLOW.md](DATA_FLOW.md) — every table there now carries a `tenant_id`.

## 5. Job identity and traceability

Job IDs remain `job_{year}_{sequence}`, globally unique (not reused per-tenant), used as the correlation ID across logs and storage paths. Storage paths now nest under tenant: `r2://{tenant_id}/{channel_id}/{job_id}/{stage}/{filename}`.

## 6. Retries and failure handling

Unchanged in mechanics from the single-tenant design (per-stage retry policy, in-place stage retry reusing upstream artifacts, dead-letter queue for out-of-band Celery failures — see prior reasoning, still valid). One addition: a stage that fails because **a tenant's own API key is invalid/expired/out of quota** is a distinct, non-retryable failure class — it should fail fast, notify the tenant specifically (not just log an internal error), and surface "reconnect your Anthropic key" style actionable messaging rather than a generic error.

## 7. Secrets and API key handling — the part that changed most

Two different kinds of secrets now exist, handled differently:

**Platform-level secrets** (env vars, same for every tenant): Supabase connection string, Redis URL, R2 credentials, `SECRET_KEY`/`ENCRYPTION_KEY`, the platform's **shared** Google OAuth client (for YouTube) and **shared** WhatsApp Business Cloud API credentials. See [ENVIRONMENT.md](ENVIRONMENT.md).

**Tenant-level secrets** (stored encrypted in the database, one set per tenant): each tenant's own Anthropic, ElevenLabs, Pexels, etc. API keys, entered via the tenant dashboard's "Connect your providers" settings page, and each tenant's own YouTube OAuth refresh token (obtained by them authorizing *through* the platform's shared Google OAuth client — see §9).

Both categories are encrypted at rest (`cryptography.Fernet`, key from `ENCRYPTION_KEY`), never returned to the frontend in plaintext after initial entry (masked, e.g. `sk-ant-...wXyz`), and RLS-scoped so a tenant's key rows are only queryable within their own session context.

- **Admin dashboard auth**: two separate auth contexts now. Tenant auth via Supabase Auth (email/password or OAuth social login — Supabase supports both) scoped to that tenant's own data via RLS. Internal ops dashboard auth is a **separate, higher-privilege** login (small, fixed set of platform operators — you, initially) that can see cross-tenant aggregate data for support/health purposes; this must never share a session/cookie namespace with tenant auth.
- **Webhook endpoints** (Meta WhatsApp delivery webhooks): verify signatures (`X-Hub-Signature-256`) before processing.
- **Untrusted content boundary**: research-stage content (scraped pages, search results) remains data, not instructions, never merged into the script-writer's instruction context in a way that lets it issue commands — same reasoning as before, now doubly important since a malicious/compromised research result could otherwise cross from one tenant's job into influencing behavior more broadly if the boundary weren't respected.
- **SSRF risk**: visual-collection stage fetches arbitrary URLs — allowlist domains, never let a fetch reach internal network addresses.
- **Cross-tenant isolation is the top security priority of this whole rewrite.** RLS policies must be written and tested (not just assumed) for every table from Phase 1 onward — see IMPLEMENTATION_PLAN.md.

## 8. Async vs. sync — unchanged

Same split as the single-tenant design: dashboard reads/config/approvals are sync request/response; topic/research/script/voice/visuals/render/thumbnail/upload/notify are queued Celery work, routed across `light` and `heavy` queues so a burst of cheap topic-generation jobs (now potentially from *many tenants at once*) never starves render capacity.

**New consideration**: queue fairness across tenants. A single tenant queuing 50 jobs shouldn't starve every other tenant's single job. Evaluate a per-tenant concurrency cap (e.g. max N jobs in-flight per tenant at once) once there's more than a handful of active tenants — flagged in IMPLEMENTATION_PLAN.md, not needed for early build/beta.

## 9. YouTube and WhatsApp: why these stay platform-level, and the scaling risk that creates

**YouTube**: a tenant doesn't create their own Google Cloud project — they authorize the **platform's single registered OAuth app** to upload to their channel (standard "connect your YouTube account" flow, like any SaaS that posts to YouTube on a user's behalf). This has two big consequences:
1. **The OAuth app must pass Google's verification for external/production use** before more than ~100 test users can connect their channel. This requires a privacy policy, a homepage, and scope justification for the YouTube upload/manage scopes, and can take real calendar time. **This is now a critical-path item, not a "start it early" nicety** — it directly blocks onboarding real (non-test) tenants. See API_REQUIREMENTS.md.
2. **YouTube Data API quota is shared across the whole platform**, not per-tenant, because it's tied to the one Google Cloud project. Default quota is 10,000 units/day; one upload costs ~1,600 units → **roughly 6 uploads/day across ALL tenants combined** on the default quota. This will not scale past a handful of active tenants without requesting a quota increase from Google — budget real lead time for this request and treat it as a hard blocker to growth, not an optimization. Track quota usage on the internal ops dashboard (§2) so this is visible before it's a crisis.

**WhatsApp**: similarly, the platform holds **one** Meta WhatsApp Business Cloud API sending number; each tenant configures their own recipient number in their profile, and notifications are sent from the shared platform number to each tenant individually. This is exactly what the Cloud API is designed for (one business number messaging many distinct recipients) — no per-tenant Meta setup needed. The scaling constraint here is milder: Meta auto-scales a number's messaging tier based on quality rating and volume, but it's still worth watching on the ops dashboard as tenant count grows.

## 10. Deployment topology (Railway + Supabase)

- **Service: `api`** — FastAPI app (orchestrator + tenant dashboard + internal ops dashboard), always-on, connects to Supabase Postgres.
- **Service: `worker-light`** — Celery worker(s), `light` queue.
- **Service: `worker-heavy`** — Celery worker(s), `heavy` queue, sized for ffmpeg.
- **Service: `beat`** — Celery Beat for scheduled jobs (per-channel posting cadence, scheduled publishing, recurring analytics pulls).
- **Managed Redis** — Railway plugin.
- **Supabase project** — Postgres + Auth, external to Railway.
- **Cloudflare R2** — external, credentials via env vars.

## 11. Copyright/licensing posture — and how BYO keys shifts responsibility

The licensing risk itself is unchanged (ARCHITECTURE.md v1 §11 reasoning still applies: stock defaults with clear commercial licenses, generative providers checked per-ToS, licensed/curated music, voice rights confirmed). **What changed**: since tenants BYO their own provider accounts, *they* are the ones with a contractual relationship to (say) their image-gen provider — the platform's job is to make the licensing status **visible and auditable per asset** (already designed into `assets.license_type` in DATA_FLOW.md) so a tenant can see and take responsibility for what they've enabled, not to silently absorb that liability on their behalf. Final QA's license-audit checklist (DATA_FLOW.md Stage 12) becomes even more important as a tenant-facing feature, not just an internal safeguard.

## 12. Monetization — explicitly deferred, not designed yet

BYO API keys means the platform doesn't need usage-based billing infrastructure to function (v1 cost is $0 in AI provider spend to the platform). Whether/how the platform itself charges tenants for access (flat subscription, free tier + paid tiers, one-time fee) is a **business decision, not an architecture blocker** — it can be added later (Stripe Checkout/Billing is a well-contained addition against the `tenants` table) once there's a working product to charge for. Not scheduled into the phased plan; add as a phase when you're ready to decide pricing.

## 13. Legal — needed before real (non-test) tenants onboard

A Terms of Service, Privacy Policy, and Acceptable Use Policy are required in practice, not just good practice, because: (a) Google's OAuth verification requires a privacy policy URL (§9), (b) the platform stores other people's YouTube access tokens and their own third-party API keys, and (c) multi-tenant content generation creates real abuse surface (see §14). These are not engineering deliverables — flag as a parallel task for the user, not something this plan builds.

## 14. Abuse and content moderation — new risk from multi-tenancy

A single-operator tool only needs to trust one operator's content choices. A multi-tenant product doesn't have that luxury:
- Some baseline content-policy check should sit in the `script_qa` stage (already a QA gate — extend its checklist to flag clearly disallowed content categories, not just quality) so the platform isn't a blind pipe for arbitrary generated content straight to YouTube.
- Rate limiting / abuse detection (a tenant spinning up mass low-quality/spam content) matters both for YouTube API quota protection (§9) and for the platform's own reputation — flagged for design attention once there's real multi-tenant traffic to observe, not solved speculatively now.

## 15. What's deliberately out of scope for v1

- Platform-hosted/billed AI provider usage (superseded by BYO keys, §12 covers the "add later if needed" path).
- Enterprise features (SSO, team seats per tenant, role-based access within a tenant's account) — v1 is one login per tenant account.
- Fully automatic trend-detection from live social listening — pluggable "trend source" provider interface, not a bespoke scraping system.
- Advanced motion graphics / style transfer in video assembly — start with template-driven ffmpeg; revisit Remotion only if that proves visually insufficient.
