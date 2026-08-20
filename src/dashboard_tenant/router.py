import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from src.dashboard_tenant.auth import SESSION_COOKIE, require_tenant
from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.config import get_settings
from src.orchestrator.db import tenant_session
from src.orchestrator.guardrails import TenantLimitExceeded, check_tenant_job_limits
from src.orchestrator.routes.channels import DEFAULT_APPROVAL_GATES, _delete_channel_cascade
from src.orchestrator.routes.tenant_keys import SUPPORTED_PROVIDERS
from src.orchestrator.security import decrypt, encrypt, mask
from src.orchestrator.storage import get_storage_provider
from src.orchestrator.supabase_auth import SupabaseAuthError, login, request_password_reset, signup
from src.orchestrator.tenants import ensure_tenant
from src.orchestrator.youtube_quota import (
    THUMBNAIL_SET_COST_UNITS,
    UPLOAD_COST_UNITS,
    QuotaExceededError,
    release_quota_reservation,
    reserve_quota_or_raise,
)
from src.providers.storage.base import StorageProvider
from src.providers.youtube.youtube_api import YouTubeAPIProvider
from src.workers.scheduler import POSTING_FREQUENCIES
from src.workers.stage_runner import (
    REJECTION_REVISION_SOURCE_STAGE,
    enqueue_stage,
    resume_after_approval,
    resume_after_rejection_with_feedback,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login_submit(email: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    try:
        result = await login(email, password)
    except SupabaseAuthError:
        return RedirectResponse("/dashboard/login?error=Invalid+email+or+password", status_code=303)

    user_id = result["user"]["id"]
    await ensure_tenant(uuid.UUID(user_id), result["user"].get("email", ""))

    response = RedirectResponse("/dashboard/overview", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, result["access_token"], httponly=True, samesite="lax", max_age=3600
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "signup.html", {"error": error, "confirm_email": None}
    )


@router.post("/signup", response_model=None)
async def signup_submit(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    try:
        result = await signup(email, password)
    except SupabaseAuthError as e:
        return templates.TemplateResponse(
            request, "signup.html", {"error": e.detail, "confirm_email": None}
        )

    # Supabase returns a session immediately only when email confirmation is
    # turned off for the project; otherwise there's no session until the
    # tenant clicks the confirmation link and logs in for the first time.
    if "access_token" in result:
        user_id = result["user"]["id"]
        await ensure_tenant(uuid.UUID(user_id), display_name)
        response = RedirectResponse("/dashboard/overview", status_code=303)
        response.set_cookie(
            SESSION_COOKIE, result["access_token"], httponly=True, samesite="lax", max_age=3600
        )
        return response

    return templates.TemplateResponse(
        request, "signup.html", {"error": None, "confirm_email": email}
    )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, sent: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": sent})


@router.post("/forgot-password")
async def forgot_password_submit(request: Request, email: str = Form(...)) -> RedirectResponse:
    # request.base_url can resolve to Railway's internal proxy address rather
    # than the public domain, which silently produced a redirect_to that
    # wasn't on Supabase's allow list — PUBLIC_BASE_URL sidesteps that
    # entirely. Falls back to request.base_url only for local dev, where
    # there's no reverse proxy in the way.
    base = get_settings().public_base_url or str(request.base_url).rstrip("/")
    redirect_to = f"{base.rstrip('/')}/dashboard/reset-password"
    await request_password_reset(email, redirect_to)
    # Always the same redirect regardless of whether the email exists — the
    # provider call above already avoids leaking that, no reason to leak it
    # here either.
    return RedirectResponse("/dashboard/forgot-password?sent=1", status_code=303)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request) -> HTMLResponse:
    # The recovery link's access_token arrives in the URL fragment (never
    # sent to any server per the URL spec), so the password update itself
    # happens client-side via JS calling Supabase directly — this route just
    # needs to hand the page the public anon key, which is meant to be
    # client-visible (same key already embedded in every other auth request
    # this app makes).
    return templates.TemplateResponse(
        request,
        "reset_password.html",
        {
            "supabase_url": get_settings().supabase_url,
            "supabase_anon_key": get_settings().supabase_anon_key,
        },
    )


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/overview", response_class=HTMLResponse)
async def overview(request: Request, tenant_id=Depends(require_tenant)) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        channel_count = (
            await session.execute(text("SELECT count(*) FROM channels"))
        ).scalar_one()
        jobs_running = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE overall_status = 'running'")
            )
        ).scalar_one()
        jobs_done_this_month = (
            await session.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE overall_status = 'done' "
                    "AND created_at >= date_trunc('month', now())"
                )
            )
        ).scalar_one()
        pending_approvals = (
            await session.execute(
                text("SELECT count(*) FROM job_stages WHERE status = 'awaiting_approval'")
            )
        ).scalar_one()
        recent_jobs = (
            (
                await session.execute(
                    text(
                        "SELECT j.id, j.current_stage, j.overall_status, j.title, "
                        "c.name AS channel_name, j.created_at "
                        "FROM jobs j JOIN channels c ON c.id = j.channel_id "
                        "ORDER BY j.created_at DESC LIMIT 6"
                    )
                )
            )
            .mappings()
            .all()
        )
        # Last 7 days of job creation, for the activity strip.
        daily_counts = (
            (
                await session.execute(
                    text(
                        "SELECT date_trunc('day', created_at) AS day, count(*) AS n "
                        "FROM jobs WHERE created_at >= now() - interval '7 days' "
                        "GROUP BY 1 ORDER BY 1"
                    )
                )
            )
            .mappings()
            .all()
        )

    counts_by_day = {row["day"].date(): row["n"] for row in daily_counts}
    today = datetime.now().date()
    activity = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        activity.append({"date": day, "count": counts_by_day.get(day, 0)})
    max_activity = max((d["count"] for d in activity), default=0) or 1

    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "active_nav": "overview",
            "channel_count": channel_count,
            "jobs_running": jobs_running,
            "jobs_done_this_month": jobs_done_this_month,
            "pending_approvals": pending_approvals,
            "recent_jobs": recent_jobs,
            "activity": activity,
            "max_activity": max_activity,
        },
    )


@router.get("/channels", response_class=HTMLResponse)
async def channels_list(
    request: Request,
    tenant_id=Depends(require_tenant),
    error: str | None = None,
    connected: str | None = None,
    job_error: str | None = None,
    deleted: str | None = None,
    uploaded: str | None = None,
) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        channels = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, niche, youtube_channel_id, "
                        "approval_gates, posting_frequency, background_music_url "
                        "FROM channels ORDER BY created_at"
                    )
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "active_nav": "channels",
            "channels": channels,
            "error": error,
            "connected": connected,
            "job_error": job_error,
            "deleted": deleted,
            "uploaded": uploaded,
            "posting_frequencies": list(POSTING_FREQUENCIES.keys()),
        },
    )


@router.post("/channels/create")
async def create_channel_submit(
    name: str = Form(...),
    niche: str = Form(""),
    audience: str = Form(""),
    language: str = Form("en"),
    style: str = Form(""),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO channels "
                "(tenant_id, name, niche, audience, language, style, approval_gates) "
                "VALUES (:tenant_id, :name, :niche, :audience, :language, :style, :approval_gates)"
            ),
            {
                "tenant_id": tenant_id,
                "name": name,
                "niche": niche or None,
                "audience": audience or None,
                "language": language,
                "style": style or None,
                "approval_gates": json.dumps(DEFAULT_APPROVAL_GATES),
            },
        )
    return RedirectResponse("/dashboard/channels", status_code=303)


@router.post("/channels/{channel_id}/settings")
async def update_channel_settings(
    channel_id: str,
    require_upload_approval: str = Form("off"),
    require_topic_approval: str = Form("off"),
    require_script_approval: str = Form("off"),
    posting_frequency: str = Form(""),
    background_music_url: str = Form(""),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    # Phase 10: lets a tenant configure a "fully autonomous" channel (no
    # human gate before youtube_upload) and/or an unattended posting cadence
    # — both were previously only settable at creation time via the raw API.
    # topic_scoring/script_qa gates work the same way (stage_runner already
    # supported them; this was just never exposed in the settings form).
    approval_gates = {
        "youtube_upload": require_upload_approval == "on",
        "topic_scoring": require_topic_approval == "on",
        "script_qa": require_script_approval == "on",
    }
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "UPDATE channels SET approval_gates = :gates, posting_frequency = :freq, "
                "background_music_url = :music_url WHERE id = :id"
            ),
            {
                "gates": json.dumps(approval_gates),
                "freq": posting_frequency or None,
                "music_url": background_music_url or None,
                "id": channel_id,
            },
        )
    return RedirectResponse("/dashboard/channels", status_code=303)


@router.post("/channels/{channel_id}/delete")
async def delete_channel_submit(
    channel_id: uuid.UUID, tenant_id=Depends(require_tenant)
) -> RedirectResponse:
    async with tenant_session(tenant_id) as session:
        found = await _delete_channel_cascade(session, channel_id)
    if not found:
        return RedirectResponse("/dashboard/channels?error=Channel+not+found", status_code=303)
    return RedirectResponse("/dashboard/channels?deleted=1", status_code=303)


@router.post("/channels/{channel_id}/jobs/create")
async def create_job_submit(
    channel_id: str, topic_brief: str = Form(""), tenant_id=Depends(require_tenant)
) -> RedirectResponse:
    try:
        await check_tenant_job_limits(tenant_id)
    except TenantLimitExceeded as e:
        return RedirectResponse(
            f"/dashboard/channels?job_error={quote(e.detail)}", status_code=303
        )

    async with tenant_session(tenant_id) as session:
        channel = (
            (
                await session.execute(
                    text("SELECT id FROM channels WHERE id = :id"), {"id": channel_id}
                )
            )
            .mappings()
            .first()
        )
        if channel is None:
            return RedirectResponse(
                "/dashboard/channels?job_error=Channel+not+found", status_code=303
            )

        seq = (await session.execute(text("SELECT nextval('job_id_seq')"))).scalar_one()
        job_id = f"job_{datetime.now().year}_{seq:05d}"
        first_stage = PIPELINE_STAGES[0]
        await session.execute(
            text(
                "INSERT INTO jobs "
                "(id, tenant_id, channel_id, current_stage, overall_status, topic_brief) "
                "VALUES (:id, :tenant_id, :channel_id, :stage, 'running', :topic_brief)"
            ),
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "channel_id": channel_id,
                "stage": first_stage,
                "topic_brief": topic_brief.strip() or None,
            },
        )

    enqueue_stage(job_id, str(tenant_id), first_stage)
    return RedirectResponse(f"/dashboard/jobs/{job_id}", status_code=303)


@router.post("/channels/{channel_id}/upload-video")
async def upload_video_submit(
    channel_id: str,
    video: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    thumbnail: UploadFile | None = File(None),
    privacy_status: str = Form("private"),
    publish_at_utc: str = Form(""),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    """A tenant's own already-made video, not something the AI pipeline
    generated — skips topic_generation..final_qa entirely and reuses only
    the real, already-built OAuth/resumable-upload plumbing. Still recorded
    as a `jobs` row (current_stage/overall_status stamped as already 'done')
    purely to satisfy youtube_videos.job_id's FK and keep it visible
    alongside AI-generated uploads in analytics — no pipeline stage ever
    runs against it."""
    async with tenant_session(tenant_id) as session:
        channel = (
            (
                await session.execute(
                    text(
                        "SELECT youtube_refresh_token_encrypted FROM channels WHERE id = :id"
                    ),
                    {"id": channel_id},
                )
            )
            .mappings()
            .first()
        )
    if channel is None:
        return RedirectResponse(
            "/dashboard/channels?job_error=Channel+not+found", status_code=303
        )
    if not channel["youtube_refresh_token_encrypted"]:
        return RedirectResponse(
            "/dashboard/channels?job_error="
            + quote("Connect a YouTube account for this channel before uploading."),
            status_code=303,
        )

    video_bytes = await video.read()
    thumbnail_bytes = (
        await thumbnail.read() if thumbnail is not None and thumbnail.filename else None
    )
    tags_list = [t.strip() for t in tags.split(",") if t.strip()]
    # A disabled <select> (see channels.html — scheduling disables it
    # client-side) simply isn't submitted by the browser, so an empty
    # publish_at_utc is the only signal needed for "publish now" vs
    # "scheduled"; privacy_status's Form default covers the missing field.
    publish_at = publish_at_utc.strip() or None

    async with tenant_session(tenant_id) as session:
        seq = (await session.execute(text("SELECT nextval('job_id_seq')"))).scalar_one()
        job_id = f"job_{datetime.now().year}_{seq:05d}"
        await session.execute(
            text(
                "INSERT INTO jobs "
                "(id, tenant_id, channel_id, current_stage, overall_status, title, "
                " description, tags) "
                "VALUES (:id, :tenant_id, :channel_id, 'youtube_upload', 'done', :title, "
                " :description, :tags)"
            ),
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "channel_id": channel_id,
                "title": title,
                "description": description or None,
                "tags": json.dumps(tags_list),
            },
        )
        await session.commit()

    upload_cost = UPLOAD_COST_UNITS + (THUMBNAIL_SET_COST_UNITS if thumbnail_bytes else 0)
    try:
        reservation_id = await reserve_quota_or_raise(
            str(tenant_id), job_id, "videos.insert", upload_cost
        )
    except QuotaExceededError as e:
        return RedirectResponse(f"/dashboard/channels?job_error={quote(str(e))}", status_code=303)

    try:
        refresh_token = decrypt(channel["youtube_refresh_token_encrypted"])
        provider = YouTubeAPIProvider()
        result = await provider.upload_video(
            refresh_token=refresh_token,
            video_bytes=video_bytes,
            title=title,
            description=description,
            tags=tags_list,
            privacy_status=privacy_status,
            thumbnail_bytes=thumbnail_bytes,
            publish_at=publish_at,
        )
    except Exception as e:
        await release_quota_reservation(reservation_id)
        return RedirectResponse(
            f"/dashboard/channels?job_error={quote(f'Upload failed: {e}')}", status_code=303
        )

    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO youtube_videos "
                "(tenant_id, job_id, channel_id, youtube_video_id, url, "
                " scheduled_publish_at, uploaded_at, status) "
                "VALUES (:tenant_id, :job_id, :channel_id, :video_id, :url, "
                " :scheduled_publish_at, now(), :status)"
            ),
            {
                "tenant_id": tenant_id,
                "job_id": job_id,
                "channel_id": channel_id,
                "video_id": result.video_id,
                "url": result.url,
                "scheduled_publish_at": (
                    # naive-UTC, matching every other timestamp column in
                    # this schema (see timeutil.utcnow_naive) — asyncpg
                    # rejects a tz-aware value against `timestamp without
                    # time zone`.
                    datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
                    .astimezone(UTC)
                    .replace(tzinfo=None)
                    if publish_at
                    else None
                ),
                "status": "scheduled" if publish_at else "uploaded",
            },
        )
        await session.commit()

    return RedirectResponse("/dashboard/channels?uploaded=1", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(
    request: Request, tenant_id=Depends(require_tenant), channel_id: str | None = None
) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        channels = (
            (await session.execute(text("SELECT id, name FROM channels ORDER BY created_at")))
            .mappings()
            .all()
        )
        query = (
            "SELECT j.id, j.channel_id, c.name AS channel_name, j.current_stage, "
            "j.overall_status, j.title, j.created_at "
            "FROM jobs j JOIN channels c ON c.id = j.channel_id "
        )
        params: dict = {}
        if channel_id:
            query += "WHERE j.channel_id = :channel_id "
            params["channel_id"] = channel_id
        query += "ORDER BY j.created_at DESC LIMIT 100"
        jobs = (await session.execute(text(query), params)).mappings().all()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "active_nav": "jobs",
            "jobs": jobs,
            "channels": channels,
            "selected_channel_id": channel_id,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request, job_id: str, tenant_id=Depends(require_tenant)
) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        job = (
            (
                await session.execute(
                    text(
                        "SELECT j.id, j.channel_id, c.name AS channel_name, j.current_stage, "
                        "j.overall_status, j.title, j.description, j.created_at "
                        "FROM jobs j JOIN channels c ON c.id = j.channel_id WHERE j.id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .first()
        )
        if job is None:
            return templates.TemplateResponse(
                request,
                "job_detail.html",
                {"active_nav": "jobs", "job": None, "stages": []},
                status_code=404,
            )
        stage_rows = (
            (
                await session.execute(
                    text(
                        "SELECT stage, status, started_at, finished_at, error FROM job_stages "
                        "WHERE job_id = :id ORDER BY started_at NULLS LAST"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .all()
        )

    by_stage: dict[str, dict] = {}
    for row in stage_rows:
        by_stage[row["stage"]] = dict(row)

    stages = [
        by_stage.get(name, {"stage": name, "status": "not_started", "started_at": None,
                             "finished_at": None, "error": None})
        for name in PIPELINE_STAGES
    ]
    return templates.TemplateResponse(
        request, "job_detail.html", {"active_nav": "jobs", "job": job, "stages": stages}
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_list(request: Request, tenant_id=Depends(require_tenant)) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        videos = (
            (
                await session.execute(
                    text(
                        "SELECT v.id, v.url, v.uploaded_at, j.title, c.name AS channel_name "
                        "FROM youtube_videos v "
                        "JOIN jobs j ON j.id = v.job_id "
                        "JOIN channels c ON c.id = v.channel_id "
                        "WHERE v.uploaded_at IS NOT NULL "
                        "ORDER BY v.uploaded_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )

        rows = []
        max_views = 0
        for v in videos:
            snapshots = (
                (
                    await session.execute(
                        text(
                            "SELECT metrics FROM analytics_snapshots "
                            "WHERE youtube_video_id = :id"
                        ),
                        {"id": v["id"]},
                    )
                )
                .mappings()
                .all()
            )
            by_day = {
                s["metrics"]["day"]: s["metrics"] for s in snapshots if s["metrics"].get("day")
            }
            latest = by_day.get(30) or by_day.get(7) or by_day.get(1)
            if latest:
                max_views = max(max_views, latest["views"])
            rows.append({**v, "by_day": by_day, "latest": latest})

    return templates.TemplateResponse(
        request,
        "analytics.html",
        {"active_nav": "analytics", "rows": rows, "max_views": max_views or 1},
    )


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_list(request: Request, tenant_id=Depends(require_tenant)) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        pending = (
            (
                await session.execute(
                    text(
                        "SELECT js.id, js.job_id, js.stage, j.channel_id, c.name AS channel_name "
                        "FROM job_stages js "
                        "JOIN jobs j ON j.id = js.job_id "
                        "JOIN channels c ON c.id = j.channel_id "
                        "WHERE js.status = 'awaiting_approval' "
                        "ORDER BY js.job_id"
                    )
                )
            )
            .mappings()
            .all()
        )

        items = []
        for p in pending:
            detail: dict = {}
            if p["stage"] in ("topic_scoring",):
                candidates = (
                    (
                        await session.execute(
                            text(
                                "SELECT id, title, hook, angle, score FROM topics "
                                "WHERE job_id = :job_id AND status = 'candidate' "
                                "ORDER BY score DESC"
                            ),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .all()
                )
                detail["candidates"] = candidates
            if p["stage"] == "script_qa":
                script = (
                    (
                        await session.execute(
                            text(
                                "SELECT version, content, word_count FROM scripts "
                                "WHERE job_id = :job_id ORDER BY version DESC LIMIT 1"
                            ),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                qa = (
                    (
                        await session.execute(
                            text(
                                "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                                "AND stage = 'script_qa' AND status = 'done' "
                                "ORDER BY started_at DESC LIMIT 1"
                            ),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                detail["script"] = script
                detail["qa_feedback"] = (
                    (qa["output_ref"] if qa else {}).get("feedback") if qa else None
                )
            if p["stage"] == "youtube_upload":
                job_meta = (
                    (
                        await session.execute(
                            text("SELECT title, description, tags FROM jobs WHERE id = :job_id"),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                final_qa_row = (
                    (
                        await session.execute(
                            text(
                                "SELECT output_ref FROM job_stages WHERE job_id = :job_id "
                                "AND stage = 'final_qa' AND status = 'done' "
                                "ORDER BY started_at DESC LIMIT 1"
                            ),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                video_asset = (
                    (
                        await session.execute(
                            text(
                                "SELECT storage_path FROM assets WHERE job_id = :job_id "
                                "AND type = 'video_final' ORDER BY created_at DESC LIMIT 1"
                            ),
                            {"job_id": p["job_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                detail["job_meta"] = job_meta
                detail["final_qa"] = final_qa_row["output_ref"] if final_qa_row else None
                detail["video_storage_path"] = video_asset["storage_path"] if video_asset else None
            items.append({**p, "detail": detail})

    return templates.TemplateResponse(
        request,
        "approvals.html",
        {
            "active_nav": "approvals",
            "items": items,
            "revisable_stages": set(REJECTION_REVISION_SOURCE_STAGE.keys()),
        },
    )


@router.get("/jobs/{job_id}/video-preview")
async def job_video_preview(
    job_id: str, request: Request, tenant_id=Depends(require_tenant)
) -> Response:
    """Lets a tenant actually watch the rendered video before approving the
    youtube_upload gate — the approvals page previously only showed the raw
    storage_path string. tenant_session's RLS scoping is what stops a tenant
    from previewing a job that isn't theirs, same as every other route here.
    Supports Range requests so the <video> player can seek."""
    async with tenant_session(tenant_id) as session:
        asset = (
            (
                await session.execute(
                    text(
                        "SELECT storage_path FROM assets WHERE job_id = :job_id "
                        "AND type = 'video_final' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )
    if asset is None:
        raise HTTPException(status_code=404, detail="No preview video available for this job")

    storage = get_storage_provider()
    video_bytes = await storage.download_bytes(
        StorageProvider.key_from_storage_path(asset["storage_path"])
    )
    total = len(video_bytes)

    range_header = request.headers.get("range")
    if range_header:
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else total - 1
        except ValueError:
            start, end = 0, total - 1
        end = min(end, total - 1)
        chunk = video_bytes[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )
    return Response(
        content=video_bytes,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(total)},
    )


@router.get("/approvals/count")
async def approvals_pending_count(tenant_id=Depends(require_tenant)) -> dict:
    """Polled from base.html's sidebar badge so a tenant sees a job is
    waiting on them without having to navigate to the Approvals page."""
    async with tenant_session(tenant_id) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM job_stages WHERE status = 'awaiting_approval'")
            )
        ).scalar_one()
    return {"count": count}


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_list(request: Request, tenant_id=Depends(require_tenant)) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT message_type, detail, sent_at FROM notifications_sent "
                        "ORDER BY sent_at DESC LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(
        request, "notifications.html", {"active_nav": "notifications", "items": rows}
    )


@router.post("/approvals/{job_id}/{stage}")
async def approve_submit(
    job_id: str,
    stage: str,
    decision: str = Form(...),
    notes: str = Form(""),
    selected_topic_id: str = Form(""),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    notes_clean: str | None = notes.strip() or None
    # Notes turn a rejection into "here's what to change" instead of a dead
    # end — reruns the stage that produced what got rejected
    # (script_writing/topic_generation/visual_generation — see
    # REJECTION_REVISION_SOURCE_STAGE), which reads these same notes back
    # out of the approvals row once committed. A gate not in that mapping
    # always just fails outright. Determined up front, purely from
    # stage/notes, so the DB write below (inside the transaction) and the
    # actual enqueue (after it commits — same ordering resume_after_approval
    # already relies on, so the worker never reads the notes before they're
    # actually committed) agree on the outcome.
    can_revise = bool(notes_clean) and stage in REJECTION_REVISION_SOURCE_STAGE
    # A human picking a specific candidate (approvals.html's radio buttons,
    # defaulted to the top-scored one) bypasses topic_scoring.py's own
    # highest-score auto-pick entirely — previously "Approve" only ever meant
    # "let the algorithm decide", with no way to actually choose which video
    # gets made even with the gate on.
    topic_id_clean = selected_topic_id.strip() or None
    topic_override = stage == "topic_scoring" and decision == "approved" and topic_id_clean

    async with tenant_session(tenant_id) as session:
        pending = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM job_stages WHERE job_id = :job_id AND stage = :stage "
                        "AND status = 'awaiting_approval'"
                    ),
                    {"job_id": job_id, "stage": stage},
                )
            )
            .mappings()
            .first()
        )
        if pending is not None:
            await session.execute(
                text(
                    "UPDATE approvals SET resolved_at = now(), resolved_by = 'tenant', "
                    "decision = :decision, notes = :notes WHERE job_id = :job_id "
                    "AND stage = :stage AND resolved_at IS NULL"
                ),
                {"decision": decision, "notes": notes_clean, "job_id": job_id, "stage": stage},
            )
            await session.execute(
                text("DELETE FROM job_stages WHERE id = :id"), {"id": pending["id"]}
            )
            if topic_override:
                await session.execute(
                    text(
                        "UPDATE topics SET status = 'rejected' WHERE job_id = :job_id "
                        "AND status = 'candidate'"
                    ),
                    {"job_id": job_id},
                )
                await session.execute(
                    text("UPDATE topics SET status = 'selected' WHERE id = :id"),
                    {"id": topic_id_clean},
                )
                await session.execute(
                    text("UPDATE jobs SET topic_id = :topic_id WHERE id = :job_id"),
                    {"topic_id": topic_id_clean, "job_id": job_id},
                )
            elif decision != "approved" and not can_revise:
                await session.execute(
                    text("UPDATE jobs SET overall_status = 'failed' WHERE id = :job_id"),
                    {"job_id": job_id},
                )

    if pending is not None:
        if topic_override:
            next_stage = PIPELINE_STAGES[PIPELINE_STAGES.index("topic_scoring") + 1]
            enqueue_stage(job_id, str(tenant_id), next_stage)
        elif decision == "approved":
            await resume_after_approval(job_id, str(tenant_id), stage)
        elif can_revise:
            resume_after_rejection_with_feedback(job_id, str(tenant_id), stage)
    return RedirectResponse("/dashboard/approvals", status_code=303)


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_list(
    request: Request, tenant_id=Depends(require_tenant), saved: str | None = None
) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT provider, status, encrypted_key "
                        "FROM tenant_api_keys ORDER BY provider"
                    )
                )
            )
            .mappings()
            .all()
        )
    keys = {
        r["provider"]: {"status": r["status"], "masked_key": mask(decrypt(r["encrypted_key"]))}
        for r in rows
    }
    providers = sorted(SUPPORTED_PROVIDERS)
    return templates.TemplateResponse(
        request,
        "api_keys.html",
        {"active_nav": "api-keys", "providers": providers, "keys": keys, "saved": saved},
    )


@router.post("/api-keys")
async def api_keys_submit(
    provider: str = Form(...), api_key: str = Form(...), tenant_id=Depends(require_tenant)
) -> RedirectResponse:
    provider = provider.lower()
    if provider not in SUPPORTED_PROVIDERS or not api_key.strip():
        return RedirectResponse("/dashboard/api-keys", status_code=303)

    encrypted = encrypt(api_key.strip())
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO tenant_api_keys (tenant_id, provider, encrypted_key, status) "
                "VALUES (:tenant_id, :provider, :encrypted_key, 'unvalidated') "
                "ON CONFLICT (tenant_id, provider) DO UPDATE "
                "SET encrypted_key = EXCLUDED.encrypted_key, status = 'unvalidated'"
            ),
            {"tenant_id": tenant_id, "provider": provider, "encrypted_key": encrypted},
        )
    return RedirectResponse(f"/dashboard/api-keys?saved={provider}", status_code=303)
