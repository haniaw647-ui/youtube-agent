# Data Flow

How data moves through the pipeline, stage by stage: inputs, outputs, storage location, and DB tables touched. Read alongside [ARCHITECTURE.md](ARCHITECTURE.md) §4 (state machine) and the multi-tenant model in §1/§7.

**Every table below carries a `tenant_id`, and every table has a Row-Level Security policy restricting reads/writes to that tenant's own session context (Supabase Auth `auth.uid()` mapped to `tenant_id` via a membership table).** This is the single most important line in this document — it's the mechanism that makes multi-tenancy actually safe, not just organizationally tidy.

## Conventions

- **DB** = row(s) written/updated in Postgres (Supabase).
- **Storage** = object written to Cloudflare R2, path pattern `r2://{tenant_id}/{channel_id}/{job_id}/{stage}/{filename}`.
- **In** / **Out** = logical data in and out of the stage.

---

## Stage -1 — Tenant signup

**In**: email/password or social login via Supabase Auth.
**Out**: `tenants` row + Supabase `auth.users` row linked via `tenant_id`.
**DB**: `tenants` (id, auth_user_id, display_name, created_at, status).

## Stage -0.5 — Provider key connection (tenant settings, not job-scoped)

**In**: tenant enters their own API keys for the providers they want to use (Anthropic, ElevenLabs/OpenAI TTS, Pexels/Pixabay, image-gen, etc.) in their dashboard settings.
**Out**: encrypted key rows.
**DB**: `tenant_api_keys` (tenant_id, provider, encrypted_key, added_at, last_validated_at, status). Keys are validated with a lightweight test call at save time so a bad key fails at settings-save, not mid-job.

## Stage 0 — Channel configuration & YouTube connect

**In**: tenant creates a channel config (niche, audience, language, video length, style, posting frequency, topics-to-avoid, approval-gate toggles per stage) and authorizes the platform's shared Google OAuth app against their actual YouTube channel (ARCHITECTURE.md §9).
**Out**: `channels` row; encrypted YouTube refresh token attached to that channel.
**DB**: `channels` (id, tenant_id, name, niche, audience, language, video_length_target, style, posting_frequency, approval_gates JSON, provider_config JSON, youtube_channel_id, youtube_refresh_token_encrypted, whatsapp_recipient_number, created_at).

This is the "configure once" step from the main goal — everything downstream reads from this row, and it's now explicitly tenant-owned.

---

## Stage 1 — Topic generation

**In**: `channels` row + `topics` history for this channel (avoid repeats) + tenant's own Anthropic key + optional trend signal input.
**Out**: N topic candidates (`title`, `hook`, `angle`, `audience`, `estimated_interest`, `uniqueness_score`, `difficulty`, `evergreen`).
**DB**: `topics` rows (tenant_id, channel_id, job_id nullable until selected, title, hook, angle, scores, status=`candidate`, created_at).
**Cost**: charged to the tenant's own Anthropic account, not the platform's — logged for their visibility, not for platform billing (`api_call_logs.cost_usd` is informational, not invoiced).

## Stage 2 — Topic scoring & selection

**In**: candidate `topics` rows.
**Out**: `topic_score = interest + uniqueness + audience_relevance + search_potential + retention_potential - competition`; top topic promoted, `Job` row created (`job_{year}_{seq}`, globally unique).
**DB**: `topics.status` → `selected`/`rejected`; new `jobs` row (id, tenant_id, channel_id, topic_id, current_stage=`research`, overall_status=`running`, created_at).
**Approval gate (optional)**: if `channels.approval_gates.topic` is enabled, pauses at `awaiting_approval`.

## Stage 3 — Research

**In**: selected topic + tenant's own search-API key.
**Out**: structured research notes (facts, sources, suggested stats/quotes).
**Storage**: `r2://{tenant}/{channel}/{job}/research/notes.json`.
**DB**: `job_stages` row; API calls logged in `api_call_logs`.
**Security note**: fetched web content is data, not instructions — never merged into the script-writer's context as executable direction (ARCHITECTURE.md §7).

## Stage 4 — Script writing

**In**: topic + research notes + channel style/tone config + target length + tenant's Anthropic key.
**Out**: full scene-segmented script text, estimated spoken duration.
**Storage**: `r2://{tenant}/{channel}/{job}/script/v1.md` (versioned across QA revisions).
**DB**: `scripts` row (tenant_id, job_id, version, content, word_count, est_duration_seconds, status=`draft`).

## Stage 5 — Script QA

**In**: latest script version.
**Out**: factual-consistency check against research, tone/style check, pacing/length check, **baseline content-policy check** (ARCHITECTURE.md §14 — new for multi-tenant), flags list; passes or requests revision (looped back to Stage 4, capped, then escalated to human).
**DB**: `scripts.status` updated; `job_stages` entry.
**Approval gate (optional)**: pauses at `awaiting_approval`, script shown in tenant dashboard for edit/approve/reject.

## Stage 6a — Voice-over (parallel with 6b)

**In**: approved script + tenant's voice-provider key (ElevenLabs/OpenAI/Azure, per their `provider_config`).
**Out**: narration audio + timestamps.
**Storage**: `r2://{tenant}/{channel}/{job}/voice/narration.mp3`.
**DB**: `assets` row (tenant_id, job_id, type=`voice`, storage_path, duration_seconds, provider, cost).

## Stage 6b — Visual generation/collection (parallel with 6a)

**In**: script's scene segments + tenant's visual-provider config/keys.
**Out**: one visual asset per scene segment, with `source` recording provider + license type.
**Storage**: `r2://{tenant}/{channel}/{job}/visuals/segment_{n}.{ext}`.
**DB**: `assets` rows (tenant_id, job_id, type=`visual`, segment_index, storage_path, provider, license_type, cost).

Both 6a/6b complete (Celery `chord`) before Stage 7.

## Stage 7 — Video assembly

**In**: narration audio + visual assets + segment timing + channel's template/pacing rules.
**Out**: assembled video, no subtitles/music yet.
**Storage**: `r2://{tenant}/{channel}/{job}/render/assembled.mp4`.
**DB**: `assets` row (type=`video_draft`).

## Stage 8 — Subtitle burn-in

**In**: assembled video + narration timing (Whisper-aligned if needed).
**Out**: subtitle track + captioned video.
**Storage**: `r2://{tenant}/{channel}/{job}/render/captions.srt`, `.../with_captions.mp4`.

## Stage 9 — Background music

**In**: captioned video + channel's music library config.
**Out**: final mixed video (music ducked under narration).
**Storage**: `r2://{tenant}/{channel}/{job}/render/final.mp4`.
**DB**: `assets` row (type=`video_final`); `license_type`/`attribution_text` recorded for Stage 11.

## Stage 10 — Thumbnail generation

**In**: topic title/hook + channel branding config (colors, logo, font) + tenant's image-gen key (if generative mode) or Canva credentials (if template mode).
**Out**: 1+ thumbnail candidates (1280x720).
**Storage**: `r2://{tenant}/{channel}/{job}/thumbnail/candidate_{n}.png`.
**DB**: `assets` rows (type=`thumbnail`).

## Stage 11 — Metadata generation

**In**: script + topic + channel SEO config + Stage 9 attribution requirements.
**Out**: title candidates, description (incl. attribution text), tags.
**DB**: `jobs` row updated with `title`, `description`, `tags` (JSON).

## Stage 12 — Final QA

**In**: final video + thumbnail + metadata + full per-job asset license audit trail.
**Out**: pass/fail + checklist (audio sync, resolution/aspect ratio, no visual glitches, no unresolved-license assets, metadata within YouTube limits, thumbnail text legible, content-policy recheck).
**DB**: `job_stages` entry with checklist result JSON.
**Approval gate (default-on)**: recommended default human checkpoint, per-tenant configurable, before anything reaches the platform's shared YouTube OAuth app (ARCHITECTURE.md §9).

## Stage 13 — YouTube upload

**In**: final video + thumbnail + metadata + **the channel's stored refresh token, used against the platform's shared OAuth client** + scheduling preference.
**Out**: YouTube video ID/URL, upload status.
**DB**: `youtube_videos` row (tenant_id, job_id, channel_id, youtube_video_id, url, scheduled_publish_at, uploaded_at, status).
**Quota cost**: ~1,600 units against the **platform-wide** shared quota (ARCHITECTURE.md §9) — this is the one place tenant isolation doesn't apply to the resource itself, only to the data. Track cumulative daily usage across all tenants, not just per-tenant, since the ceiling is shared.

## Stage 14 — WhatsApp notification

**In**: job completion event + `youtube_videos` row if successful + channel's configured `whatsapp_recipient_number`.
**Out**: WhatsApp message sent from the platform's single shared sending number to that tenant's configured recipient, via the pre-approved template.
**DB**: `notifications_sent` row (tenant_id, job_id, channel, message_type, status, sent_at).

## Stage 15 — Analytics tracking (recurring, not on the critical path)

**In**: `youtube_videos` rows past `uploaded_at`, on a schedule (+1/+7/+30 days).
**Out**: views, watch time, retention curve, CTR, likes/comments snapshot.
**DB**: `analytics_snapshots` rows (tenant_id, youtube_video_id, snapshot_at, metrics JSON).
**Trigger**: Celery Beat, reads YouTube Analytics API using the relevant channel's stored token.

---

## Cross-cutting: `api_call_logs`

One row per external API call: `tenant_id`, `job_id` (nullable), `stage`, `provider`, `endpoint`, `request_summary`, `response_summary`, `status`, `cost_usd`, `duration_ms`, `created_at`. Now serves two audiences: the tenant (their own cost/usage visibility, since they're paying their own provider bills) and the internal ops dashboard (aggregate platform health, and specifically shared-resource usage like YouTube quota consumption across tenants).

## Cross-cutting: `approvals`

`tenant_id`, `job_id`, `stage`, `requested_at`, `resolved_at`, `resolved_by` (tenant user or `auto_rule`), `decision`, `notes`. Audit trail of what a human reviewed before publishing, per tenant.

## Cross-cutting: `tenant_api_keys`

`tenant_id`, `provider`, `encrypted_key`, `added_at`, `last_validated_at`, `status` (`valid`/`invalid`/`unvalidated`). RLS-scoped — a tenant can only ever see/manage their own rows; the internal ops dashboard sees connection *status* per tenant (for support purposes: "is this tenant's Anthropic key broken") but never the decrypted key value itself.
