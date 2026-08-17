# Project Status

Living document — update this in the same commit/session as any implementation change. Don't let it drift.

## Where things stand

**Phase 8 (WhatsApp notifications) complete.** Phases 0 through 8 are all done, deployed, and passing their test suites — 53 automated tests total. **All 15 pipeline stages now have real implementations — no stubs remain anywhere in the pipeline.** What's left is entirely credentials/setup, not code: a real end-to-end run needs a tenant's own AI provider keys, an R2 bucket, a Google OAuth client, and Meta WhatsApp credentials — see "Immediate next steps."

### Phase 8 — WhatsApp notifications
- **WhatsApp Cloud API provider** (`src/providers/whatsapp/whatsapp_api.py`): template-based send only, since Meta requires pre-approved templates for any business-initiated message — free-form text isn't an option here. Platform-shared sending number; each channel supplies its own recipient number.
- **Real `whatsapp_notification` stage**: on a successful job, sends the published video's title + URL to the channel's configured number. Soft-skips (returns `{"skipped": True}`, does not fail the job) if no recipient number is set yet — notification setup was never meant to be a hard prerequisite for the pipeline to complete.
- **Terminal-failure notifications, which the pipeline had no path for at all before this phase**: a permanently-failed stage used to just stop silently, with nothing marking the job failed or telling the tenant. Celery's `light`/`heavy` tasks now check `self.request.retries >= self.max_retries` inside the exception handler — true only on the final exhausted retry, not on every intermediate retry — and on that condition call `notify_job_failure()` (`src/workers/failure_notify.py`), which sets `jobs.overall_status = 'failed'` and sends a WhatsApp failure template. Notification-send errors are swallowed there so a broken WhatsApp integration can never mask the original pipeline failure.
- **Webhook endpoint** (`/whatsapp/webhook`, `src/orchestrator/routes/whatsapp_webhook.py`): `GET` handles Meta's subscription-verification handshake; `POST` receives delivery-status callbacks with real HMAC-SHA256 verification of the `X-Hub-Signature-256` header (`src/orchestrator/whatsapp_webhook.py::verify_signature`) before trusting any payload. Delivery statuses correlate back to the original send via a new `provider_message_id` column on `notifications_sent`.
- **`whatsapp_recipient_number`** (already existed as an unused Phase 1 schema column) is now exposed in channel create/update (`PATCH /channels/{id}`) and has a working dashboard form under `/dashboard/channels`.
- **Message template needed for Meta approval** — submit this exact template for review before any real send will work: name `job_status_update`, category `UTILITY`, language `en_US`, body `"Update on your video job {{1}}: {{2}}. {{3}}"` (params: job title, status, video URL).
- **Deployed**: `api`, `worker-light`, `worker-heavy` all redeployed on commit `633c60c` and confirmed `SUCCESS`; `/health` verified returning 200 live.

### Phase 7 — YouTube publishing
- **Per-tenant YouTube OAuth connect flow**: `/channels/{id}/youtube/connect` redirects to Google, `/youtube/callback` exchanges the code and stores the encrypted refresh token. The callback is a browser redirect with no bearer token, so tenant/channel identity travels sealed in an OAuth `state` param via the existing Fernet encryption infra — tamper-proof, since only a state we ourselves sealed will decrypt back to valid IDs
- **YouTube upload provider** using `google-api-python-client` (resumable upload + credential refresh — exactly the kind of thing worth trusting to Google's own client rather than hand-rolling), wrapped in `asyncio.to_thread` since the library is sync-only
- **Platform-wide YouTube quota tracking** (ARCHITECTURE.md §9): a `quota_units` column on `api_call_logs`, and a pre-upload budget check that fails loudly if today's cumulative usage across *all* tenants would exceed Google's shared 10,000-unit daily ceiling — rather than letting Google's API reject it later with a less actionable error
- **Real `youtube_upload` stage**: downloads the final video + thumbnail from R2, decrypts the channel's refresh token, uploads with metadata, records quota usage, inserts the `youtube_videos` row. Defaults to `privacyStatus: private` — a human already reviewed the content via `final_qa`'s gate, but defaulting a brand-new automated pipeline's uploads to fully public immediately is the wrong failure mode to risk; channels can override via `provider_config` once trusted
- **Minimal channels dashboard page** with a working "Connect YouTube" button — needed to actually demo the connect flow through the UI rather than raw API calls
- **Found and fixed a real, previously-undetected production bug**: every `job_stages`/`jobs` timestamp write in `stage_runner.py` used a timezone-*aware* `datetime.now(UTC)` against columns that are `timestamp without time zone` — asyncpg rejects that combination outright. This had survived every phase up to now because the isolation test stubs out `enqueue_stage`, so `execute_stage`'s real DB writes were never actually exercised end to end — it would have broken *every single stage execution* the moment a real pipeline finally ran. Fixed with a shared `utcnow_naive()` helper and confirmed against the real DB with a new regression test, not just by inspection.
- **Deployed**: all four services redeployed and healthy; `/health` and `/dashboard/channels` (redirects to login when unauthenticated, as expected) both verified live
- **Next** (at the time): Phase 8 — WhatsApp notifications. (Now complete, see above.)

### Phase 6 — final QA and the pre-upload human approval gate
- **`probe_video_info`** (ffprobe): resolution + audio/video stream presence — real-tested locally, same pattern as Phase 4's other ffmpeg helpers
- **Real `final_qa` stage**: resolution/aspect-ratio check, A/V stream presence, YouTube metadata length limits (title/description/tags), a license audit across every asset for the job (ARCHITECTURE.md §11's audit trail, now actually enforced rather than just recorded), and a content-policy recheck that reuses `script_qa`'s flags instead of spending a second LLM call on the same question
- **Hard gate**: a failed checklist — including any unresolved license, which currently means Phase 4's placeholder music on every job — always forces human sign-off before `youtube_upload`, regardless of what the channel's own `approval_gates` config says. A clean pass still respects the channel's own gate setting.
- **New channels now default to `approval_gates={"youtube_upload": true}`** — IMPLEMENTATION_PLAN Phase 6's "gate defaults on for every new channel, tenant-overridable," implemented via `ChannelCreate.approval_gates`
- **Dashboard approval queue extended**: the `youtube_upload` gate now renders the checklist pass/fail breakdown, license issues, content flags, and a title/description/tags preview, alongside the existing topic-candidate and script-review views
- **Not built this phase, and deliberately so**: actual YouTube OAuth/upload code. Phase 6 builds the checklist and gate mechanics; Phase 7 is where the platform's Google OAuth app and the real upload call get wired in. Google's OAuth app verification (ARCHITECTURE.md §9) remains the standing external dependency for that — I have no visibility into whether it's been started; flagged again below.
- **Deployed**: all four services redeployed and healthy; `/health` and `/dashboard/login` both verified live

### Phase 5 — thumbnail generation and metadata
- **Pillow-based thumbnail compositing** (`src/workers/thumbnail_utils.py`): crops a stock base image to 1280x720, adds a dark gradient band and wrapped bold title text — real, unmocked test coverage, same reasoning as Phase 4's ffmpeg tests (pure local compute, no credentials needed)
- **Real `thumbnail_generation` stage**: Pexels base image (same default-stock-first reasoning as `visual_generation`), composited, uploaded to R2 with `license_type` populated
- **Real `metadata_generation` stage**: LLM call producing title candidates, description, and tags — pulls `background_music`'s attribution text and includes it verbatim in the description when present, writes directly to `jobs.title`/`description`/`tags`
- **Canva Connect evaluated and consciously not pursued**: the implementation plan suggested prototyping it, but it needs per-tenant OAuth (not a pasted API key like every other provider), a materially bigger scope item than this phase calls for — and the Canva MCP connector available in this session isn't something the deployed backend could call at runtime regardless. Stock + Pillow meets the "ready to review" bar; templated/branded thumbnails remain a real option if a tenant needs it later.
- Bundled `fonts-dejavu-core` in the Dockerfile for text rendering — permissively licensed (Bitstream Vera License), not fetched/bundled without checking
- **Deployed**: all four services redeployed and healthy

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

All 15 pipeline stages are code-complete. Nothing further is blocked on me writing code — every remaining item below is credentials, an external account, or a decision only you can make.

1. **You, to unblock a live demo of the whole pipeline**: four separate things now need your input at once —
   a. A tenant signed up (email confirmed) with Anthropic + Tavily + ElevenLabs (or OpenAI) + Pexels keys connected via the API key vault.
   b. Cloudflare R2 bucket + API token (Account ID, Access Key ID, Secret Access Key) — I have no Cloudflare connector, so this genuinely needs your hands.
   c. A Google Cloud project with the YouTube Data API v3 enabled, an OAuth 2.0 Client ID + Secret (type "Web application"), and a redirect URI registered as `https://api-production-f6f23.up.railway.app/youtube/callback`. Send me the Client ID and Secret and I'll wire them into Railway as `YOUTUBE_OAUTH_CLIENT_ID`/`YOUTUBE_OAUTH_CLIENT_SECRET`/`YOUTUBE_OAUTH_REDIRECT_URI`, same as I did for the DB password.
   d. **New this phase**: Meta WhatsApp Business Cloud API credentials — a phone number ID, a permanent access token, and the `job_status_update` template (text above) submitted and approved by Meta. Send me the phone number ID and access token and I'll wire them into Railway as `WHATSAPP_PHONE_NUMBER_ID`/`WHATSAPP_ACCESS_TOKEN`. Without this, `whatsapp_notification` will keep soft-skipping (harmless) and failure notifications simply won't send (less harmless — you won't hear about a failed job except by checking the dashboard).
2. **You, separately, to unblock onboarding *real* (non-test) tenants later**: the Google OAuth app above only needs to exist for testing — Google's *external/production verification* is the much bigger lead-time item, needed only before more than ~100 test users can connect. Start it whenever you're ready to move past internal testing; not needed for the demo in step 1. See ARCHITECTURE.md §9.
3. **You**: add `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SECRET_KEY`, `ENCRYPTION_KEY` as GitHub Actions repo secrets (same values as your local `.env`) so CI can actually run the isolation test on every push.
4. **You, eventually**: decide on a real licensed music source (curated CC0 set vs. a paid API) — not urgent, but nothing from `background_music` is publish-safe until this is resolved, and `final_qa` will keep correctly blocking every job on it until it is.
5. **Legal, before real tenants onboard**: Terms of Service, Privacy Policy, Acceptable Use Policy (ARCHITECTURE.md §13).
6. **Me, once you have a direction**: Phase 9 (analytics/ops dashboard), Phase 10 (scheduling/hardening), or Phase 11 (monetization) — none started, none requested yet.

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
- **2026-08-17**: Phase 4 complete — ffmpeg orchestration for video assembly (Ken-Burns pacing scaled to real audio duration), subtitle burn-in, and background music mixing; resolved the open Whisper-mode decision by realizing it isn't needed (we already know the narration text, only timing needed estimating); shipped a clearly-flagged placeholder music tone pending a real licensed source; installed ffmpeg locally to run a real unmocked integration test — the first phase verified with genuine end-to-end execution rather than mocks, since video assembly needs no external credentials; redeployed and verified live.
- **2026-08-17**: Phase 5 complete — Pillow-based thumbnail compositing (real, unmocked test coverage, same reasoning as Phase 4); real thumbnail_generation (Pexels base + overlay) and metadata_generation (title/description/tags, includes music attribution) stages; evaluated and consciously skipped Canva Connect (needs per-tenant OAuth, out of scope for what this phase needs, and the MCP connector isn't runtime-callable by the backend anyway); bundled a permissively-licensed font for text rendering; redeployed and verified live. 28 tests passing overall.
- **2026-08-17**: Phase 6 complete — real final_qa checklist (resolution, A/V streams, metadata limits, license audit, content-flag recheck) with a hard gate that forces human approval before youtube_upload on any failure or unresolved license, independent of channel config; new channels now default to the youtube_upload gate being on; dashboard approval queue extended to show the checklist, license issues, and metadata preview; 38 tests passing overall; redeployed and verified live.
- **2026-08-17**: Phase 7 complete — per-tenant YouTube OAuth connect flow (state sealed via Fernet since the callback carries no bearer token); YouTube upload provider via google-api-python-client; platform-wide quota tracking with a pre-upload budget check; real youtube_upload stage defaulting to private visibility; minimal channels dashboard page with a working connect button. Found and fixed a real production bug along the way — every job_stages/jobs timestamp write used a tz-aware datetime against tz-naive columns, which asyncpg rejects outright; this had never been caught because the isolation test stubs out the code path that writes them, so it would have broken every real stage execution the first time the pipeline actually ran. Fixed and covered with a regression test against the real DB. 47 tests passing overall; redeployed and verified live.
- **2026-08-17**: Phase 8 complete — WhatsApp Cloud API provider and real whatsapp_notification stage (soft-skips if no recipient configured); terminal-failure notifications added where none existed before (Celery tasks now detect the final exhausted retry and mark the job failed + notify, instead of failing silently); webhook endpoint with real HMAC-SHA256 signature verification for delivery-status callbacks; whatsapp_recipient_number exposed in the channel API and dashboard. **All 15 pipeline stages are now real — no stubs remain anywhere in the pipeline.** 53 tests passing overall; redeployed on commit 633c60c and verified live (`/health` returns 200 on all three services). Meta WhatsApp credentials (phone number ID, access token, approved `job_status_update` template) are now the standing blocker for real sends, alongside the existing tenant-keys/R2/Google-OAuth needs.
