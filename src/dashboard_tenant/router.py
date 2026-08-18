import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from src.dashboard_tenant.auth import SESSION_COOKIE, require_tenant
from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import tenant_session
from src.orchestrator.guardrails import TenantLimitExceeded, check_tenant_job_limits
from src.orchestrator.routes.channels import DEFAULT_APPROVAL_GATES
from src.orchestrator.routes.tenant_keys import SUPPORTED_PROVIDERS
from src.orchestrator.security import decrypt, encrypt, mask
from src.orchestrator.supabase_auth import SupabaseAuthError, login, signup
from src.orchestrator.tenants import ensure_tenant
from src.workers.scheduler import POSTING_FREQUENCIES
from src.workers.stage_runner import enqueue_stage, resume_after_approval

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
) -> HTMLResponse:
    async with tenant_session(tenant_id) as session:
        channels = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, niche, youtube_channel_id, whatsapp_recipient_number, "
                        "approval_gates, posting_frequency "
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
    posting_frequency: str = Form(""),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    # Phase 10: lets a tenant configure a "fully autonomous" channel (no
    # human gate before youtube_upload) and/or an unattended posting cadence
    # — both were previously only settable at creation time via the raw API.
    approval_gates = {"youtube_upload": require_upload_approval == "on"}
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "UPDATE channels SET approval_gates = :gates, posting_frequency = :freq "
                "WHERE id = :id"
            ),
            {
                "gates": json.dumps(approval_gates),
                "freq": posting_frequency or None,
                "id": channel_id,
            },
        )
    return RedirectResponse("/dashboard/channels", status_code=303)


@router.post("/channels/{channel_id}/whatsapp")
async def set_whatsapp_number(
    channel_id: str,
    whatsapp_recipient_number: str = Form(...),
    tenant_id=Depends(require_tenant),
) -> RedirectResponse:
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text("UPDATE channels SET whatsapp_recipient_number = :number WHERE id = :id"),
            {"number": whatsapp_recipient_number or None, "id": channel_id},
        )
    return RedirectResponse("/dashboard/channels", status_code=303)


@router.post("/channels/{channel_id}/jobs/create")
async def create_job_submit(
    channel_id: str, tenant_id=Depends(require_tenant)
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
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :tenant_id, :channel_id, :stage, 'running')"
            ),
            {"id": job_id, "tenant_id": tenant_id, "channel_id": channel_id, "stage": first_stage},
        )

    enqueue_stage(job_id, str(tenant_id), first_stage)
    return RedirectResponse(f"/dashboard/jobs/{job_id}", status_code=303)


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
                                "SELECT title, hook, angle, score FROM topics "
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
        request, "approvals.html", {"active_nav": "approvals", "items": items}
    )


@router.post("/approvals/{job_id}/{stage}")
async def approve_submit(
    job_id: str, stage: str, decision: str = Form(...), tenant_id=Depends(require_tenant)
) -> RedirectResponse:
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
                    "decision = :decision WHERE job_id = :job_id AND stage = :stage "
                    "AND resolved_at IS NULL"
                ),
                {"decision": decision, "job_id": job_id, "stage": stage},
            )
            await session.execute(
                text("DELETE FROM job_stages WHERE id = :id"), {"id": pending["id"]}
            )
            if decision != "approved":
                await session.execute(
                    text("UPDATE jobs SET overall_status = 'failed' WHERE id = :job_id"),
                    {"job_id": job_id},
                )

    if pending is not None and decision == "approved":
        await resume_after_approval(job_id, str(tenant_id), stage)
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
