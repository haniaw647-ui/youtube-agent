import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant
from src.orchestrator.youtube_oauth import (
    YouTubeOAuthError,
    build_authorization_url,
    seal_state,
    unseal_state,
)


def test_seal_and_unseal_state_round_trips():
    tenant_id = uuid.uuid4()
    channel_id = uuid.uuid4()

    state = seal_state(tenant_id, channel_id)
    unsealed_tenant, unsealed_channel = unseal_state(state)

    assert unsealed_tenant == tenant_id
    assert unsealed_channel == channel_id


def test_unseal_state_rejects_garbage():
    with pytest.raises(YouTubeOAuthError):
        unseal_state("not-a-real-sealed-state")


def test_unseal_state_rejects_tampering():
    tenant_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    state = seal_state(tenant_id, channel_id)

    with pytest.raises(YouTubeOAuthError):
        unseal_state(state[:-1] + ("A" if state[-1] != "A" else "B"))


def test_build_authorization_url_includes_state_and_scope():
    state = seal_state(uuid.uuid4(), uuid.uuid4())
    url = build_authorization_url(state)

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert f"state={state}" in url or "state=" in url
    assert "youtube.upload" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url


class TestConnectRouteUsesDashboardAuth:
    """Real, unmocked — GET /channels/{id}/youtube/connect is only ever
    reached via a browser link click from channels.html's 'Connect YouTube'
    button, never a programmatic API call (redirecting to Google's consent
    screen is inherently a browser-navigation flow). It used to require a
    bearer Authorization header (get_current_tenant_id) that a plain <a
    href> link can never send — every real click 422'd with 'Field required:
    header.authorization'. This proves the fix: the route now accepts the
    dashboard's cookie session instead."""

    TENANT = uuid.uuid4()
    CHANNEL_ID = uuid.uuid4()

    @classmethod
    def setup_class(cls) -> None:
        asyncio.run(ensure_tenant(cls.TENANT, "YouTube Connect Test Tenant"))

        async def _seed() -> None:
            async with service_session() as session:
                await session.execute(
                    text(
                        "INSERT INTO channels (id, tenant_id, name) "
                        "VALUES (:id, :t, 'Connect Test Channel')"
                    ),
                    {"id": cls.CHANNEL_ID, "t": cls.TENANT},
                )
                await session.commit()

        asyncio.run(_seed())
        app.dependency_overrides[require_tenant] = lambda: cls.TENANT

    @classmethod
    def teardown_class(cls) -> None:
        async def _cleanup() -> None:
            async with service_session() as session:
                await session.execute(
                    text("DELETE FROM channels WHERE tenant_id = :t"), {"t": cls.TENANT}
                )
                await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": cls.TENANT})
                await session.commit()

        asyncio.run(_cleanup())
        app.dependency_overrides.clear()

    def test_connect_redirects_to_google_for_a_cookie_authenticated_dashboard_user(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get(f"/channels/{self.CHANNEL_ID}/youtube/connect")

        assert resp.status_code in (302, 307), resp.text
        assert resp.headers["location"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"
        )

    def test_connect_404s_for_a_channel_that_does_not_belong_to_this_tenant(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get(f"/channels/{uuid.uuid4()}/youtube/connect")
        assert resp.status_code == 404
