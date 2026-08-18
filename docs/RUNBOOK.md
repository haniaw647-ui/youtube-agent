# Operator Runbook

IMPLEMENTATION_PLAN.md Phase 10. Five scenarios, written against how the system actually behaves today (verified by reading the real code paths, not aspirational). Where a gap exists, it's called out explicitly rather than papered over.

All "check the ops dashboard" steps assume `/admin` (internal ops dashboard, Phase 9) — shared operator password, ask if you need it.

---

## 1. A tenant's job is stuck

**Symptom**: a job's `overall_status` stays `running` (or `awaiting_approval` with nobody expecting to review it) far longer than the pipeline should take. Check `/admin/jobs` — sorted by `created_at`, a job whose `current_stage` hasn't advanced in hours is the signal.

**Diagnosis**:
1. `/admin/jobs` → find the job, note its `current_stage`.
2. Check `/admin/failures` — if it's there, it already failed terminally and the tenant should have gotten a WhatsApp notification (see §4 if they say they didn't).
3. If it's *not* in failures but also not progressing, query `job_stages` directly for that `job_id`, ordered by `started_at`. The most recent row's `status`:
   - `awaiting_approval` — working as designed. The tenant hasn't reviewed it yet (check the channel's `approval_gates`). Not a bug; a nudge to the tenant is the fix.
   - `running`, with no corresponding `done`/`failed` row and no newer stage — **this is the real "stuck" case.**

**Root cause of the real stuck case — a known gap, not yet fixed**: Celery's default `task_acks_late = False` is unchanged in `src/workers/celery_app.py`. A task is acknowledged (removed from Redis) the moment a worker *receives* it, before it runs — so if the worker process dies mid-task (OOM, `docker kill`, Railway redeploy interrupting an in-flight task), the task is already gone from the broker. It is never retried, `execute_stage`'s `except` block never runs (the process is dead, not raising a Python exception), and the `job_stages` row it inserted at the *start* of the stage (`_insert_running_stage`) sits at `status='running'` forever. There is currently no automated stale-job detector.

**Manual recovery** (until the gap above is closed):
1. Confirm via Railway logs (`worker-light` or `worker-heavy`, whichever queue the stuck stage routes to — see `HEAVY_STAGES` in `stage_runner.py`) that there's no worker actively processing it — no matching `job_id` in recent log lines.
2. Mark the orphaned `job_stages` row `failed` directly:
   ```sql
   UPDATE job_stages SET status = 'failed', finished_at = now(),
     error = 'Manually marked stuck — worker likely crashed mid-task'
   WHERE id = '<the stuck row's id>';
   ```
3. Either re-enqueue the same stage (`enqueue_stage(job_id, tenant_id, stage)` from a Python shell against production) to retry it, or mark the job failed and let the tenant re-run manually via a fresh job if the underlying work isn't easily resumable.

**Real fix, not yet applied** (flagging for a deliberate decision, not applying unilaterally — it changes retry semantics platform-wide): set `celery_app.conf.task_acks_late = True`. This makes Celery redeliver a task if the worker dies before acking, closing the stuck-job hole. Trade-off: if a worker crashes *after* doing real work (e.g., after an LLM call succeeded) but *before* acking, the task redelivers and reruns from the top — for most stages this just means a duplicate `job_stages` row and a second provider API call (real cost, since these are tenant BYO keys). Worth doing, but the cost/reliability trade-off should be a conscious call, not a silent config flip.

---

## 2. A worker crashed

**Symptom**: `worker-light` or `worker-heavy`'s Railway deployment shows as unhealthy, or jobs routed to that queue stop progressing platform-wide (distinct from §1 — this is *every* job on that queue, not one).

**Diagnosis**:
1. Railway → check the service's deployment status and logs. A crash-loop shows repeated `Starting Container` lines close together.
2. Common causes already hit once in this project's history: OOM from Celery's default CPU-count concurrency (fixed in Phase 1 by capping `--concurrency` explicitly — see the Railway start commands; if this regresses after any redeploy that resets the start command, that's the first thing to check).
3. Check memory usage in Railway's metrics — `worker-heavy` runs ffmpeg (video assembly), which is the most memory-intensive stage; a large job or a burst of concurrent heavy jobs is the most likely OOM trigger.

**Resolution**:
1. If crash-looping: fix the root cause (usually a config/memory issue) and redeploy — see PROJECT_STATUS.md's deployment pattern (`railway-agent` to force a fresh build, since auto-deploy-on-push isn't reliable here).
2. Jobs that were mid-stage when the worker died: same manual-recovery procedure as §1 (they're the same underlying gap — no `acks_late`).
3. Once the worker is back up and healthy, no further action needed for jobs that hadn't started yet — they're still queued in Redis and will be picked up normally.

---

## 3. Platform-wide YouTube quota exceeded

**Symptom**: `youtube_upload` stages start failing with "Platform-wide YouTube API daily quota would be exceeded" (this is a deliberate, loud failure — see `youtube_upload.py` — not Google silently rejecting the call). This is a **platform-wide incident**, not one tenant's problem: the quota is shared across every tenant (ARCHITECTURE.md §9), so once it's tripped, *every* tenant's uploads are blocked until it clears.

**Diagnosis**: `/admin/quota` — shows today's cumulative usage against the 10,000-unit default ceiling and the alert threshold (Phase 9). At ~1,600 units/upload, this is roughly 6 uploads/day platform-wide before it trips.

**Resolution**:
1. **Immediate**: nothing to do — it clears automatically at UTC midnight (the quota window in `get_todays_quota_usage()` is calendar-day-scoped). Affected jobs stay at their `youtube_upload` stage's failed attempt; Celery's retry/backoff will keep re-attempting up to `max_retries=3` and then terminally fail (with a WhatsApp notification if configured) — so if the quota clears before those retries exhaust, the job may actually recover on its own. If it doesn't (retries exhausted before midnight), the tenant needs to be told to expect a delayed upload, and the job's `youtube_upload` stage needs a manual re-enqueue after midnight.
2. **Structural**: if this happens often, it means real growth past what the default Google Cloud quota supports. Request a quota increase from Google (Cloud Console → APIs & Services → YouTube Data API v3 → Quotas) — budget real lead time for this, it's not instant. See ARCHITECTURE.md §9.
3. Since the Phase 10 concurrency fix (`reserve_quota_or_raise`, see `youtube_quota.py`), a burst of concurrent uploads can no longer *overshoot* the ceiling — they'll correctly serialize and the ones that don't fit will fail cleanly with the same message, rather than silently succeeding past the limit.

---

## 4. WhatsApp template rejected (or any WhatsApp send failure)

**Symptom**: a tenant reports never getting a job-completion/failure WhatsApp message despite having a `whatsapp_recipient_number` configured.

**Diagnosis**:
1. Query `notifications_sent` for that `job_id` — a row with `status = 'send_failed'` confirms the platform *tried* and Meta's API rejected it (`whatsapp_notification.py` and `failure_notify.py` both swallow send errors deliberately, so a failure here never masks the underlying job outcome — but it does mean silent-to-the-tenant unless you check this table).
2. Common rejection causes: the `job_status_update` template isn't approved yet in Meta Business Manager (check its review status there), the recipient number is malformed (not E.164) or hasn't opted in / messaged the business number first (WhatsApp's 24-hour session window rules for non-template messages don't apply here since this is template-based, but a *rejected* template submission would block every send), or `WHATSAPP_ACCESS_TOKEN` expired/was revoked.
3. Check Railway logs for the `api`/`worker-light` service around the failure timestamp — `WhatsAppCloudAPIProvider.send_template_message`'s underlying HTTP error (from Meta's API) is what actually raised, and the exception is swallowed but not currently logged with full detail (see the improvement note below).

**Resolution**:
1. Fix the root cause in Meta Business Manager (resubmit template, refresh token, verify number format).
2. There's no automatic re-send of a failed notification — the job's own outcome is unaffected, so nothing pipeline-side needs to happen. If the tenant needs to know their job's outcome now, check `/admin/jobs` or `/dashboard/jobs/{id}` and tell them directly.

**Improvement worth making, not yet done**: `failure_notify.py`'s `except Exception:` swallows the actual error message without logging it — `status='send_failed'` in the DB tells you *that* it failed but not *why* without also correlating Railway logs by timestamp. Logging the exception text into `notifications_sent` (there's no column for it currently) or at least to the application logger would make this diagnosis step faster.

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
