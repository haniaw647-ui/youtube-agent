# API & Service Requirements

Two very different categories now, because of the multi-tenant + BYO-keys pivot (ARCHITECTURE.md §7):

- **Platform-level services** — the platform (you) sets these up once; cost and setup are yours.
- **Tenant-level providers** — each tenant brings their own account/key; the platform just needs to build the adapter and a "how to get a key" guide, not pay for or provision these.

## 1. Platform-level services (you set these up)

| Service | Purpose | Auth | Est. cost | Rate limits | Notes |
|---|---|---|---|---|---|
| **Supabase** | Postgres DB + Auth + Row-Level Security for all tenant data | Project API keys (anon + service role) | Free tier covers early build/beta; paid tier (~$25/mo+) once real usage/storage grows | Standard Supabase project limits | This is now core infra, not optional — see ARCHITECTURE.md §2. |
| **Railway** | Hosts API + Celery workers (light/heavy) + Beat + Redis | Project-level | Usage-based, roughly $20–60/mo for a small always-on setup, scales with worker count | — | Compute only — DB/Auth moved to Supabase. |
| **Cloudflare R2** | Object storage for all tenants' generated media | R2 API token | Storage ~$0.015/GB/mo, no egress fee | — | Multi-tenant means this scales with tenant count × videos — worth monitoring on the ops dashboard. |
| **Google Cloud project + YouTube Data/Analytics API** | Shared OAuth app every tenant authorizes to upload to their own channel | OAuth 2.0 client (one client for the whole platform) | Free | Default 10,000 units/day **shared across all tenants** (~6 uploads/day platform-wide by default) | **Critical path, not a "nice to start early" item.** Requires OAuth consent screen verification for External/Production use before more than ~100 test users can connect (needs a privacy policy URL, homepage, scope justification — real review latency). Quota increase request should be filed proactively once tenant count grows past a handful — see ARCHITECTURE.md §9. |
| **Meta WhatsApp Business Cloud API** | One shared sending number, notifies each tenant at their own configured recipient number | Meta App + WABA + permanent access token | Free tier: 1,000 business-initiated conversations/mo, then per-conversation pricing | Messaging tier scales with quality rating/volume (Meta auto-upgrades) | Needs a Meta Business Manager account, verified phone number, and one approved message template. Real setup lift but not on the same critical-path tier as the Google OAuth verification. |

## 2. Tenant-level providers (each tenant brings their own key)

The platform builds one adapter per provider (ARCHITECTURE.md §1 "provider-swappable" principle) and a short in-dashboard guide for getting a key. No cost or quota planning needed on the platform side — this is entirely the tenant's own account.

| Capability | Default provider | Alternative(s) the adapter interface should support | What the tenant needs |
|---|---|---|---|
| Script LLM | Anthropic (Claude) | — (single default is fine for v1; interface still swappable later) | An Anthropic API key from console.anthropic.com |
| Research/search | Tavily or Serper | either, tenant picks in settings | An API key from their chosen provider (both have free/low tiers) |
| Voice-over | ElevenLabs | OpenAI TTS, Azure Speech | A key from their chosen provider |
| Visuals (stock) | Pexels | Pixabay | A free API key — lowest-friction default, should be the pre-selected option for new tenants |
| Visuals (generative, opt-in) | none by default | Replicate/Ideogram/OpenAI images | Only needed if a tenant explicitly enables generative visuals — **flag per-provider commercial-use/YouTube-monetization terms in the connection UI**, since this is now the tenant's own liability to accept (ARCHITECTURE.md §11), not something the platform can quietly guarantee on their behalf |
| Visuals (AI video, opt-in) | none by default | Runway/Kling/Luma | Same as above — opt-in, tenant's own account, clear licensing disclosure at connection time |
| Subtitle alignment | Whisper via OpenAI API (default) | self-hosted `faster-whisper` (platform-run, no tenant key needed) | If API mode: tenant's OpenAI key (or share their existing one if they use OpenAI TTS) |
| Music | curated/licensed library (platform-provided, not tenant-provided — see note) | Epidemic Sound API (tenant's own account) | v1 default: platform ships a small curated, license-cleared music set usable by all tenants (this one thing stays platform-provided since sourcing correctly-licensed music per-tenant is unnecessary friction for a low-differentiation feature) |
| Thumbnails | same as visuals (generative) + Pillow overlay | Canva Connect | Tenant's image-gen key, or their own Canva account if template mode is used |

## 3. Setup checklist, in order

**Platform side (you), before Phase 0 realistically needs them:**

1. Supabase project created (Phase 0).
2. Railway project + Redis (Phase 0).
3. Cloudflare R2 bucket (Phase 3, when real media starts getting stored).
4. **Google Cloud project + OAuth client — start this immediately, in parallel with Phase 0/1 build work**, because verification review time is the single longest external lead time in the whole project and directly blocks onboarding anyone beyond test users.
5. Meta Business Manager + WhatsApp Cloud API — start during Phase 1/2, template approval has meaningful but shorter lead time than Google's verification.

**Tenant side, self-serve, whenever they sign up:**

1. Create account (Supabase Auth signup).
2. Connect provider keys they want to use (at minimum: Anthropic, one voice provider, Pexels is free/instant so pre-wired as default).
3. Create a channel, authorize their YouTube channel via the platform's OAuth flow.
4. Set their WhatsApp notification number.

No tenant should ever need to wait on the platform operator to get a provider account working — the whole point of BYO keys is self-serve onboarding. The one place a tenant *can* be blocked on the platform is YouTube connect, if the platform's Google OAuth app hasn't finished verification yet — which is exactly why item 4 above is called out as the platform's top-priority external dependency.
