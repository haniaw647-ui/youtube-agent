from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from src.dashboard_tenant.auth import SESSION_COOKIE, require_tenant
from src.orchestrator.db import tenant_session
from src.orchestrator.supabase_auth import SupabaseAuthError, login
from src.workers.stage_runner import resume_after_approval

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

    response = RedirectResponse("/dashboard/approvals", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, result["access_token"], httponly=True, samesite="lax", max_age=3600
    )
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


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
            items.append({**p, "detail": detail})

    return templates.TemplateResponse(request, "approvals.html", {"items": items})


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
