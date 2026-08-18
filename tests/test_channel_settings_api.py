"""Real, unmocked — proves PATCH /channels/{id} only touches fields the
caller actually sent (Phase 10 added approval_gates and posting_frequency to
what was previously whatsapp_recipient_number-only). A naive implementation
that always overwrote every column would silently wipe a tenant's other
settings on any partial update; this is exactly the kind of bug that only
shows up with a real multi-step sequence against a real DB, not by inspection.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient

from src.orchestrator.main import app
from src.orchestrator.supabase_auth import get_current_tenant_id
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()

client = TestClient(app)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Channel Settings API Test Tenant"))
    app.dependency_overrides[get_current_tenant_id] = lambda: TENANT


def teardown_module(_module: object) -> None:
    from sqlalchemy import text

    from src.orchestrator.db import service_session

    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


def test_partial_updates_never_clobber_untouched_fields() -> None:
    resp = client.post(
        "/channels",
        json={
            "name": "Partial Update Test Channel",
            "whatsapp_recipient_number": "+15551234567",
            "approval_gates": {"youtube_upload": True},
        },
    )
    assert resp.status_code == 200, resp.text
    channel_id = resp.json()["id"]

    resp = client.patch(f"/channels/{channel_id}", json={"posting_frequency": "weekly"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["posting_frequency"] == "weekly"
    assert body["approval_gates"] == {"youtube_upload": True}  # untouched
    assert body["whatsapp_recipient_number"] == "+15551234567"  # untouched

    resp = client.patch(
        f"/channels/{channel_id}", json={"approval_gates": {"youtube_upload": False}}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approval_gates"] == {"youtube_upload": False}
    assert body["posting_frequency"] == "weekly"  # untouched by this call
    assert body["whatsapp_recipient_number"] == "+15551234567"  # still untouched


def test_empty_patch_is_rejected_rather_than_silently_no_op() -> None:
    resp = client.post("/channels", json={"name": "Empty Patch Test Channel"})
    channel_id = resp.json()["id"]

    resp = client.patch(f"/channels/{channel_id}", json={})
    assert resp.status_code == 400
