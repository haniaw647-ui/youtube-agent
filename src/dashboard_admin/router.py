from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from src.dashboard_admin.auth import (
    SESSION_COOKIE,
    SESSION_LIFETIME_SECONDS,
    check_password,
    make_session_cookie,
    require_admin,
)
from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.orchestrator.youtube_quota import DEFAULT_DAILY_QUOTA, get_todays_quota_usage

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login_submit(password: str = Form(...)) -> RedirectResponse:
    if not check_password(password):
        return RedirectResponse("/admin/login?error=Incorrect+password", status_code=303)

    response = RedirectResponse("/admin/jobs", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(),
        httponly=True,
        samesite="lax",
        max_age=SESSION_LIFETIME_SECONDS,
    )
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/jobs", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def jobs_list(request: Request) -> HTMLResponse:
    async with service_session() as session:
        jobs = (
            (
                await session.execute(
                    text(
                        "SELECT j.id, j.current_stage, j.overall_status, j.created_at, "
                        "c.name AS channel_name, t.display_name AS tenant_name "
                        "FROM jobs j "
                        "JOIN channels c ON c.id = j.channel_id "
                        "JOIN tenants t ON t.id = j.tenant_id "
                        "ORDER BY j.created_at DESC LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})


@router.get("/failures", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def failures_list(request: Request) -> HTMLResponse:
    async with service_session() as session:
        failures = (
            (
                await session.execute(
                    text(
                        "SELECT j.id, c.name AS channel_name, t.display_name AS tenant_name, "
                        "js.stage AS failed_stage, js.error AS error "
                        "FROM jobs j "
                        "JOIN channels c ON c.id = j.channel_id "
                        "JOIN tenants t ON t.id = j.tenant_id "
                        "LEFT JOIN LATERAL ( "
                        "  SELECT stage, error FROM job_stages "
                        "  WHERE job_id = j.id AND status = 'failed' "
                        "  ORDER BY started_at DESC LIMIT 1 "
                        ") js ON true "
                        "WHERE j.overall_status = 'failed' "
                        "ORDER BY j.updated_at DESC LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(request, "failures.html", {"failures": failures})


@router.get("/quota", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def quota_page(request: Request) -> HTMLResponse:
    used = await get_todays_quota_usage()
    threshold = get_settings().youtube_quota_alert_threshold
    pct = round(used / DEFAULT_DAILY_QUOTA * 100, 1)
    return templates.TemplateResponse(
        request,
        "quota.html",
        {
            "used": used,
            "total": DEFAULT_DAILY_QUOTA,
            "pct": pct,
            "threshold": threshold,
            "over_threshold": used / DEFAULT_DAILY_QUOTA >= threshold,
        },
    )


@router.get("/tenants", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def tenants_list(request: Request) -> HTMLResponse:
    async with service_session() as session:
        tenants = (
            (
                await session.execute(
                    text(
                        "SELECT id, display_name, created_at FROM tenants "
                        "ORDER BY created_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
        keys = (
            (
                await session.execute(
                    text(
                        "SELECT tenant_id, provider, status FROM tenant_api_keys "
                        "ORDER BY tenant_id, provider"
                    )
                )
            )
            .mappings()
            .all()
        )

    keys_by_tenant: dict = {}
    for k in keys:
        keys_by_tenant.setdefault(k["tenant_id"], []).append(k)

    rows = [{**t, "provider_keys": keys_by_tenant.get(t["id"], [])} for t in tenants]
    return templates.TemplateResponse(request, "tenants.html", {"tenants": rows})


@router.get("/abuse", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def abuse_list(request: Request) -> HTMLResponse:
    async with service_session() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT js.job_id, j.tenant_id, c.name AS channel_name, "
                        "t.display_name AS tenant_name, js.output_ref "
                        "FROM job_stages js "
                        "JOIN jobs j ON j.id = js.job_id "
                        "JOIN channels c ON c.id = j.channel_id "
                        "JOIN tenants t ON t.id = j.tenant_id "
                        "WHERE js.stage = 'script_qa' "
                        "AND jsonb_array_length(js.output_ref->'flags') > 0 "
                        "ORDER BY js.started_at DESC LIMIT 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    flagged = [{**r, "flags": r["output_ref"].get("flags", [])} for r in rows]
    return templates.TemplateResponse(request, "abuse.html", {"flagged": flagged})
