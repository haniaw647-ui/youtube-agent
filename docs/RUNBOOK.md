# Operator Runbook

IMPLEMENTATION_PLAN.md Phase 10. Five scenarios, written against how the system actually behaves today (verified by reading the real code paths, not aspirational). Where a gap exists, it's called out explicitly rather than papered over.

All "check the ops dashboard" steps assume `/admin` (internal ops dashboard, Phase 9) — shared operator password, ask if you need it.

---

## 1. A tenant's job is stuck

**Symptom**: a job's `overall_status` stays `running` (or `awaiting_approval` with nobody expecting to review it) far longer than the pipeline should take. Check `/admin/jobs` — sorted by `created_at`, a job whose `current_stage` hasn't advanced in hours is the signal.

**Diagnosis**:
1. `/admin/jobs` → find the job, note its `current_stage`.
2. Check `/admin/failures` — if it's there, it already failed terminally and the tenant should have a notification on `/dashboard/notifications` (see §4 if they say they didn't).
3. If it's *not* in failures but also not progressing, query `job_stages` directly for that `job_id`, ordered by `started_at`. The most recent row's `status`:
   - `awaiting_approval` — working as designed. The tenant hasn't reviewed it yet (check the channel's `approval_gates`). Not a bug; a nudge to the tenant is the fix.
   - `running`, with no corresponding `done`/`failed` row and no newer stage — **this is the real "stuck" case.**

**Root cause of the real stuck case — fixed**: Celery's default `task_acks_late = False` acknowledged a task (removed it from Redis) the moment a worker *received* it, before running it — so if the worker process died mid-task (OOM, `docker kill`, Railway redeploy interrupting an in-flight task), the task was already gone from the broker with no retry, and the `job_stages` row inserted at the *start* of the stage (`_insert_running_stage`) sat at `status='running'` forever. Fixed in `src/workers/celery_app.py`: `task_acks_late = True` (ack only after the task finishes) plus `task_reject_on_worker_lost = True` (explicit prompt requeue if the worker dies mid-task, rather than waiting on Redis's broker-level visibility timeout). **Accepted trade-off**: if a worker crashes *after* doing real work (e.g., after an LLM call already succeeded and spent a tenant's own money) but *before* acking, the task redelivers and reruns from the top — a duplicate `job_stages` row and a second provider API call. Judged worth it over silently-stuck jobs.

**If a job is still found stuck** (e.g. from before this fix deployed, or a genuinely wedged task): manual recovery —
1. Confirm via Railway logs (`worker-light` or `worker-heavy`, whichever queue the stuck stage routes to — see `HEAVY_STAGES` in `stage_runner.py`) that there's no worker actively processing it — no matching `job_id` in recent log lines.
2. Mark the orphaned `job_stages` row `failed` directly:
   ```sql
   UPDATE job_stages SET status = 'failed', finished_at = now(),
     error = 'Manually marked stuck — worker likely crashed mid-task'
   WHERE id = '<the stuck row's id>';
   ```
3. Either re-enqueue the same stage (`enqueue_stage(job_id, tenant_id, stage)` from a Python shell against production) to retry it, or mark the job failed and let the tenant re-run manually via a fresh job if the underlying work isn't easily resumable.

---

## 2. A worker crashed

**Symptom**: `worker-light` or `worker-heavy`'s Railway deployment shows as unhealthy, or jobs routed to that queue stop progressing platform-wide (distinct from §1 — this is *every* job on that queue, not one).

**Diagnosis**:
1. Railway → check the service's deployment status and logs. A crash-loop shows repeated `Starting Container` lines close together.
2. Common causes already hit once in this project's history: OOM from Celery's default CPU-count concurrency (fixed in Phase 1 by capping `--concurrency` explicitly — see the Railway start commands; if this regresses after any redeploy that resets the start command, that's the first thing to check).
3. Check memory usage in Railway's metrics — `worker-heavy` runs ffmpeg (video assembly), which is the most memory-intensive stage; a large job or a burst of concurrent heavy jobs is the most likely OOM trigger.

**Resolution**:
1. If crash-looping: fix the root cause (usually a config/memory issue) and redeploy — see PROJECT_STATUS.md's deployment pattern (`railway-agent` to force a fresh build, since auto-deploy-on-push isn't reliable here).
2. Jobs that were mid-stage when the worker died: with `task_acks_late` now on (§1), these should redeliver and rerun automatically once a healthy worker is available — check `/admin/jobs` after the worker recovers before assuming manual intervention is needed. Fall back to §1's manual recovery only if a job is still stuck after that.
3. Once the worker is back up and healthy, no further action needed for jobs that hadn't started yet — they're still queued in Redis and will be picked up normally.

---

## 3. Platform-wide YouTube quota exceeded

**Symptom**: `youtube_upload` stages start failing with "Platform-wide YouTube API daily quota would be exceeded" (this is a deliberate, loud failure — see `youtube_upload.py` — not Google silently rejecting the call). This is a **platform-wide incident**, not one tenant's problem: the quota is shared across every tenant (ARCHITECTURE.md §9), so once it's tripped, *every* tenant's uploads are blocked until it clears.

**Diagnosis**: `/admin/quota` — shows today's cumulative usage against the 10,000-unit default ceiling and the alert threshold (Phase 9). At ~1,600 units/upload, this is roughly 6 uploads/day platform-wide before it trips.

**Resolution**:
1. **Immediate**: nothing to do — it clears automatically at UTC midnight (the quota window in `get_todays_quota_usage()` is calendar-day-scoped). Affected jobs stay at their `youtube_upload` stage's failed attempt; Celery's retry/backoff will keep re-attempting up to `max_retries=3` and then terminally fail (with a `/dashboard/notifications` entry) — so if the quota clears before those retries exhaust, the job may actually recover on its own. If it doesn't (retries exhausted before midnight), the tenant needs to be told to expect a delayed upload, and the job's `youtube_upload` stage needs a manual re-enqueue after midnight.
2. **Structural**: if this happens often, it means real growth past what the default Google Cloud quota supports. Request a quota increase from Google (Cloud Console → APIs & Services → YouTube Data API v3 → Quotas) — budget real lead time for this, it's not instant. See ARCHITECTURE.md §9.
3. Since the Phase 10 concurrency fix (`reserve_quota_or_raise`, see `youtube_quota.py`), a burst of concurrent uploads can no longer *overshoot* the ceiling — they'll correctly serialize and the ones that don't fit will fail cleanly with the same message, rather than silently succeeding past the limit.

---

## 4. A tenant says they didn't get notified about a completed/failed job

**Symptom**: a tenant reports `/dashboard/notifications` is missing an entry for a job they know finished or failed. (Notifications are in-dashboard only — `src/workers/notifications.py` — there's no external delivery channel anymore, so there's no "send failure" concept to debug; the only question is whether the row got written.)

**Diagnosis**:
1. Query `notifications_sent` for that `job_id` directly. If a row exists with `message_type = 'job_completed'` or `'job_failed'`, it's on `/dashboard/notifications` — the tenant is either looking at the wrong tenant/channel or the row is further down the (most-recent-100) list than they scrolled.
2. If no row exists at all: check `jobs.overall_status` for that job. If it's still `running`/`awaiting_approval`, the job genuinely hasn't reached a terminal state yet — not a notification bug. If it's `done` or `failed` with no matching `notifications_sent` row, `notify_job_success`/`notify_job_failure` either wasn't called or raised before its `INSERT` — check Railway logs for that job_id around the completion/failure timestamp.

**Resolution**: since both notifier functions are simple DB writes with no external dependency (no third-party API, no rate limit, no delivery failure mode), a missing row after a confirmed terminal job state is a real bug worth a regression test, not a transient/expected failure the way a WhatsApp send rejection used to be.

---

## 5. A tenant's own API key expired mid-job

**Symptom**: a job fails at whichever stage calls out to the provider whose key expired (commonly `topic_generation`/`script_writing`/`script_qa` for an LLM key, `voice_over` for ElevenLabs/OpenAI, `visual_generation` for Pexels).

**Diagnosis**:
1. `/admin/failures` → find the job, check `error` — provider SDKs/HTTP clients typically surface this as a 401/403 in the error text (e.g. "invalid api key", "unauthorized"). This is already captured verbatim in `job_stages.error` (truncated to 2000 chars — see `execute_stage`'s except block).
2. Confirm by checking `/admin/tenants` → the tenant's provider key list shows `status` (currently only ever `unvalidated` — see the gap below), not whether it's *currently* working.

**Resolution**:
1. Tell the tenant: their key for that provider needs updating. They do this themselves via `/dashboard/api-keys` — no operator action needed to fix the key itself.
2. The failed job needs to be manually restarted once the key is fixed — there's currently no "retry this job with the new key" button; the tenant (or you, on their behalf) starts a fresh job via `/dashboard/channels` → "Start new video job". The partially-completed old job stays in its failed state as a record.

**Gap worth knowing about**: `tenant_api_keys.status` is set to `'unvalidated'` on save and **never updated** afterward — there's no background check that actually calls each provider to confirm a key still works, and no transition to e.g. `'invalid'` when a job fails because of it. The ops dashboard's "tenant key-connection health" (Phase 9) only ever shows the key was *saved*, not that it currently *works*. A tenant finds out their key is dead only when a job fails. Marking a key `invalid` automatically the first time a provider call 401s (and surfacing that on `/dashboard/api-keys`, not just in job failure text) would close this gap — not implemented, flagged for a future phase.

---

## General diagnostic starting points

- **`/admin/jobs`** — every job, every tenant, current stage and status.
- **`/admin/failures`** — everything with `overall_status = 'failed'`, plus the specific failed stage and error text where available.
- **`/admin/quota`** — today's YouTube quota usage.
- **`/admin/tenants`** — who's on the platform, which providers each has a key saved for.
- **`/admin/abuse`** — jobs where `script_qa` raised a content-policy flag.
- **Railway logs** (`api`, `worker-light`, `worker-heavy`) — for anything the DB-level views above don't explain, especially crashes and stack traces.
- **`job_stages` table directly** — the full history of every attempt at every stage for a job, including revision-loop reruns (`script_qa` can produce multiple rows for the same job+stage).
