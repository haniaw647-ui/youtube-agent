# Project Status

Living document — update this in the same commit/session as any implementation change. Don't let it drift.

## Where things stand

**Phase 4 (video assembly, subtitles, background music) complete.** Phases 0 through 4 are all done, deployed, and passing their test suites. This phase is unusual: it's the first one genuinely verified end-to-end with real, unmocked execution (no external credentials needed — video assembly is pure local compute). Two things still waiting on you before a live demo of the *whole* pipeline can run — a connected Anthropic/Tavily/ElevenLabs/Pexels tenant, and Cloudflare R2 credentials — see "Immediate next steps."

### Phase 4 — video assembly, subtitles, background music
- **ffmpeg orchestration helper** (`src/workers/ffmpeg_utils.py`, subprocess-based): Ken-Burns-style image sequencing scaled to real audio duration, subtitle burn-in, background music mixing/ducking
- **Real `video_assembly` stage**: downloads the voice + visual assets from R2, scales each scene's word-count-estimated duration to match the actual (ffprobe-measured) narration length so the video's timing is grounded in reality rather than a pure estimate, assembles a timed video, uploads as a `video_draft` asset
- **Real `subtitle_burn_in` stage**: SRT cue timing built from `video_assembly`'s scaled per-scene durations (not estimates alone), burns captions into the video
- **Real `background_music` stage**: mixes a track under the narration, uploads the `video_final` asset with `license_type` populated
- **Resolved the open `WHISPER_MODE` decision by realizing Whisper isn't needed at all**: we already know the exact narration text (we synthesized it from it) — there's nothing to transcribe, only timing to estimate, which word-count-proportional scaling against real audio duration handles without any API call or added dependency. `ENVIRONMENT.md` updated to explain this instead of listing a var that doesn't exist.
- **No real licensed music library exists yet** — `background_music` generates a synthesized placeholder tone, stamped `license_type='platform-placeholder-not-for-production'` so it can never be mistaken for cleared music. Sourcing real licensed tracks (API_REQUIREMENTS.md §2) is a content decision for the user, not something to fabricate.
- **Real, unmocked integration test**: synthesizes test images/audio with ffmpeg itself (no external credentials needed) and runs the actual assembly → captions → music pipeline end to end, verifying valid media comes out — stronger verification than Phase 2/3 could get, since those needed real provider keys this environment doesn't have
- Installed ffmpeg locally (via winget) specifically to make this real local testing possible; added it to CI explicitly too, rather than assuming the runner has it
- **Deployed**: all four services redeployed and healthy; `worker-heavy` (where ffmpeg actually runs) confirmed connected and ready

### Phase 3 — voice-over and visual generation/collection
- **Cloudflare R2 storage abstraction** (`providers/storage/r2.py`, S3-compatible via `aioboto3`) — platform-level credentials (not tenant BYO, since object storage is infra the platform provides regardless of which AI providers a tenant picks)
- **ElevenLabs + OpenAI TTS voice adapters**, **Pexels visual adapter** (the default stock source — free, zero licensing ambiguity)
- `script_writing`'s output now includes its parsed scene segments (narration + visual_note per scene) in `job_stages.output_ref`, so `visual_generation` can read scene-level detail back out without re-parsing the concatenated script text
- **Real `voice_over` stage**: synthesizes narration for the QA-passed script (provider selectable per channel via `provider_config.voice_provider`, default ElevenLabs), uploads to R2, records the asset
- **Real `visual_generation` stage**: searches Pexels per scene, downloads and uploads each image to R2, records one `assets` row per scene with `license_type` populated — this is the copyright-audit-trail column from ARCHITECTURE.md §11, now actually populated rather than just designed
- **Unit tests** for all four new adapters (mocked HTTP/S3, no credentials needed for CI)
- **Deployed**: all four services redeployed and healthy; `/health` verified live

**R2 credentials are the one thing I can't provision myself** — no Cloudflare connector is available in this environment, unlike Supabase/Railway. `R2_*` settings exist in code and are read from env vars, but nothing has actually been uploaded to a real bucket yet.

### Phase 2 — real text pipeline
- **Provider adapters**: `providers/llm/anthropic.py` (Claude), `providers/search/tavily.py` — both behind small swappable interfaces per the provider-swappable principle
- **`provider_keys.get_tenant_key()`**: fetches and decrypts a tenant's own BYO key for a given provider — this is what makes every stage below actually use *the tenant's* API spend, not the platform's
- **Real stage implementations** replacing the Phase 1 stubs for the first 5 pipeline stages:
  - `topic_generation`: LLM call producing scored candidates (`score = interest + uniqueness - difficulty`, evergreen bonus — a simplified version of the master prompt's 5-dimension formula, since those extra dimensions aren't separate DB columns), with duplicate prevention against the channel's full topic history
  - `topic_scoring`: selects the top-scoring candidate, marks the rest rejected
  - `research`: Tavily search on the selected topic, notes stored in `job_stages.output_ref` (R2 storage for this lands in Phase 3/4 per the plan — this is a deliberate interim choice, `output_ref` exists exactly for this purpose)
  - `script_writing`: scene-segmented script from topic + research + channel style, revision-aware (reads the latest QA feedback and addresses it when re-invoked)
  - `script_qa`: LLM QA check against research/style, with a capped revision loop (`MAX_QA_ATTEMPTS = 3`) that escalates to a human approval gate rather than looping forever or silently failing
- **Fixed a latent bug from Phase 1** while building the revision loop: `job_stages` completion updates were targeted by `(job_id, stage)`, which would have updated every prior attempt's row at once once a stage could run more than once. Now targeted by the specific row's own id.
- **Approval-gate resume logic now distinguishes two cases**: a stage gated *before* it ever ran (channel-config gate) just gets enqueued for the first time on approval; script_qa escalating *after* exhausting revisions means a human is overriding QA, which resumes at voice_over/visual_generation rather than re-running QA.
- **Minimal tenant dashboard** (`/dashboard`): cookie-based login against Supabase Auth, an approval queue showing topic candidates or script content inline, approve/reject actions. Dashboard tech decision finalized: server-rendered Jinja2/HTMX, per the ARCHITECTURE.md §3 default.
- **Unit tests** for scoring logic, JSON-response parsing (LLMs wrap "JSON only" responses in code fences anyway), and both provider adapters — all mocked, no API keys needed, safe for CI.
- **Deployed**: all four services redeployed and healthy on Railway; `/health` and `/dashboard/login` both verified live.

### Phase 0 — scaffolding
- Repo layout, FastAPI skeleton, Celery app (`light`/`heavy` queues), Docker Compose, Alembic, CI (ruff + mypy + pytest on push)
- **GitHub repo**: [haniaw647-ui/youtube-agent](https://github.com/haniaw647-ui/youtube-agent)
- **Supabase project**: `youtube-automation-platform` (ref `yfmgffojwqhodmvvwqsy`, `us-east-1`, free tier)
- **Railway project**: `youtube-automation-platform`
- Real `DATABASE_URL` wired into Railway + local `.env` (gitignored), connection verified

### Phase 1 — multi-tenant schema, RLS, auth, stub pipeline
- **13-table schema** (`tenants`, `tenant_api_keys`, `channels`, `jobs`, `job_stages`, `topics`, `scripts`, `assets`, `api_call_logs`, `approvals`, `youtube_videos`, `analytics_snapshots`, `notifications_sent`) applied to production via Alembic
- **Row-Level Security on every tenant table**, policy `tenant_id = auth.uid()` (or `id = auth.uid()` on `tenants` itself), verified directly against `pg_policies` in production
- **RLS enforcement mechanism**: `tenant_session(tenant_id)` switches the connection to the `authenticated` Postgres role and sets `request.jwt.claims` per-transaction — real database-level isolation, not app-logic-only. `service_session()` (unscoped, bypasses RLS) is for internal/worker code only. Both in [src/orchestrator/db.py](../src/orchestrator/db.py).
- **Supabase Auth wired**: signup/login proxy endpoints, bearer-token verification via `/auth/v1/user` (no JWT secret needed in the app)
- **Tenant-scoped channel CRUD** and **BYO API key vault** (Fernet-encrypted at rest, masked on read)
- **15-stage stub Celery pipeline**: sequencing, the parallel voice/visual join before assembly, retries, and approval-gate pause/resume all implemented and working
- **Automated cross-tenant isolation test** ([tests/test_tenant_isolation.py](../tests/test_tenant_isolation.py)) — two synthetic tenants exercise the real API + RLS path; proves tenant A cannot read or write tenant B's channels, jobs, or API keys. Passing against production Supabase.
- **Deployed**: `api`, `worker-light`, `worker-heavy`, `redis` all running on Railway. `worker-light` initially OOM-crash-looped (Celery's default concurrency = detected CPU count = 48, far too many prefork processes for the container) — fixed by capping concurrency at 4; `worker-heavy` was already capped at 2 and unaffected.

Not yet done: `SUPABASE_SERVICE_ROLE_KEY` (no tool available to retrieve it — needed for the internal ops dashboard's cross-tenant queries in Phase 9, not blocking); CI's isolation test needs `DATABASE_URL`/`SUPABASE_URL`/`SUPABASE_ANON_KEY`/`SECRET_KEY`/`ENCRYPTION_KEY` added as GitHub Actions secrets (same values as local `.env`) — not yet set since `gh` isn't authenticated on this machine; a full live tenant journey (real signup → email confirmation → login → create channel/job) hasn't been exercised against the deployed API specifically, since Supabase requires email confirmation and I have no way to auto-confirm without the service-role key — the isolation test covers the equivalent path via dependency injection instead, which is the standard way to test authorization logic independent of the auth provider's round trip.

Note on the DB password (Phase 0): I attempted to reset it myself via SQL (`ALTER USER postgres WITH PASSWORD ...`) using the Supabase connector so the user wouldn't need to dig through the dashboard — that specific action was blocked by an automated safety guardrail (root DB credential changes are gated regardless of tool access). The user reset it manually and provided it instead.

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
| 2026-08-17 | Tenant dashboard tech | Server-rendered **Jinja2/HTMX** (not a React SPA) — the approval-queue use case didn't need one | ARCHITECTURE.md §3, Phase 2 dashboard |

## Decisions still open (deliberately deferred)

- **Scheduling mechanism**: Celery Beat vs. Railway native scheduling — decide Phase 10 (not needed until scheduled/unattended posting).
- **Real licensed music library**: still just a placeholder tone — needs a user decision (curated CC0 set vs. a paid API like Epidemic Sound, API_REQUIREMENTS.md §2) before anything from this pipeline is publish-ready.
- **Monetization** (Phase 11, explicitly not scheduled): whether/how the platform charges tenants for access itself. BYO keys means this isn't blocking — it's a pricing decision to make once there's a working product.

## Immediate next steps

1. **You, to unblock any live demo (Phases 2-4)**: get a tenant signed up (email confirmed) with Anthropic + Tavily + ElevenLabs (or OpenAI) + Pexels keys connected via the API key vault, so a real topic → research → script → QA → voice → visuals → assembled video run can actually execute end-to-end.
2. **You, to unblock real storage (Phases 3-4)**: create a Cloudflare account (if you don't have one), an R2 bucket, and an R2 API token (Account ID, Access Key ID, Secret Access Key), and give me the bucket name + credentials — I have no Cloudflare connector in this environment, unlike Supabase/Railway, so this one genuinely needs your hands. I'll wire it into Railway the same way I did `DATABASE_URL`.
3. **You, still not started**: begin the Google Cloud OAuth consent screen → external/production verification process. This remains the **single longest external lead-time item in the whole project** and directly blocks onboarding any real (non-test) tenant — see ARCHITECTURE.md §9 and IMPLEMENTATION_PLAN.md Phase 0/6. A placeholder privacy policy page is enough to start the process.
4. **You**: begin Meta Business Manager + WhatsApp Business Cloud API setup (API_REQUIREMENTS.md §1).
5. **You**: add `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SECRET_KEY`, `ENCRYPTION_KEY` as GitHub Actions repo secrets (same values as your local `.env`) so CI can actually run the isolation test on every push.
6. **You, eventually**: decide on a real licensed music source (curated CC0 set vs. a paid API) — not urgent, but nothing from `background_music` is publish-safe until this is resolved.
7. **Legal, before real tenants onboard**: Terms of Service, Privacy Policy, Acceptable Use Policy (ARCHITECTURE.md §13).
8. **Me** (once you confirm): start Phase 5 — thumbnail generation and metadata (title/description/tags).

## Known risks to keep in view

- **Shared YouTube quota is the platform's real scaling ceiling** (ARCHITECTURE.md §9): ~6 uploads/day across ALL tenants combined on Google's default quota. Requesting an increase needs to happen proactively as tenant count grows, not reactively after uploads start failing.
- **Cross-tenant data isolation (RLS)** — proven with an automated test against production in Phase 1 (see above), not just designed. Keep extending this test as new tables/routes are added; a migration that forgets a policy is exactly the kind of regression this guards against.
- **Abuse/content-policy surface is new** with multi-tenancy — a single operator only had to trust themselves; now the platform is a pipe for other people's content choices straight to YouTube (ARCHITECTURE.md §14).
- Copyright/licensing on generated visuals/music is now partly the *tenant's* responsibility (their own provider accounts) but the platform still needs to make license status visible/auditable per asset, not silently assume it's fine (ARCHITECTURE.md §11).

## Changelog

- **2026-08-17**: Initial single-operator architecture and supporting docs written.
- **2026-08-17**: Pivoted to multi-tenant product design (other YouTubers as customers), BYO AI provider keys, Supabase Auth+RLS for tenant isolation. All six docs rewritten to reflect this. No code yet.
- **2026-08-17**: Phase 0 started — repo scaffolding, FastAPI/Celery/Alembic skeleton, Docker Compose, CI workflow all in place and verified locally (tests/lint/type-check passing). Supabase project and Railway project (+ Redis service) provisioned. Blocked on a GitHub repo to deploy the `api` service.
- **2026-08-17**: Phase 0 complete — pushed to [haniaw647-ui/youtube-agent](https://github.com/haniaw647-ui/youtube-agent), deployed `api` to Railway, verified `/health` live in production.
- **2026-08-17**: Real `DATABASE_URL` wired into Railway and local `.env`, connection verified end-to-end (local + deployed).
- **2026-08-17**: Phase 1 complete — 13-table multi-tenant schema with RLS applied to production; `tenant_session`/`service_session` DB layer proven with a real cross-tenant smoke test before building on it; Supabase Auth signup/login wired; channel CRUD + BYO API key vault; 15-stage stub Celery pipeline with parallel join and approval-gate pause/resume; automated isolation test passing against production; `api`/`worker-light`/`worker-heavy` all deployed and healthy on Railway (after fixing an OOM crash-loop in `worker-light` from Celery's default CPU-count concurrency).
- **2026-08-17**: Phase 2 complete — Anthropic + Tavily provider adapters; real topic_generation/topic_scoring/research/script_writing/script_qa stages replacing the Phase 1 stubs; script_qa's capped revision loop with human escalation; fixed a job_stages row-targeting bug the revision loop exposed; minimal server-rendered tenant dashboard with a working approval queue; unit tests for scoring/parsing/adapters (mocked, no API keys needed); redeployed and verified live.
- **2026-08-17**: Phase 3 complete — R2 storage abstraction; ElevenLabs/OpenAI TTS voice adapters and Pexels visual adapter; real voice_over and visual_generation stages populating `assets.license_type` per image; script_writing now surfaces its parsed segments for downstream stages to consume; unit tests for all four new adapters; redeployed and verified live.
- **2026-08-17**: Phase 4 complete — ffmpeg orchestration for video assembly (Ken-Burns pacing scaled to real audio duration), subtitle burn-in, and background music mixing; resolved the open Whisper-mode decision by realizing it isn't needed (we already know the narration text, only timing needed estimating); shipped a clearly-flagged placeholder music tone pending a real licensed source; installed ffmpeg locally to run a real unmocked integration test — the first phase verified with genuine end-to-end execution rather than mocks, since video assembly needs no external credentials; redeployed and verified live. Next: Phase 5 (thumbnail generation, metadata) — still no live demo of Phases 2-4 together, waiting on a connected tenant and R2 credentials from the user.
