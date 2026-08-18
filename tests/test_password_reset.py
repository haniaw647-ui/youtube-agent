"""Real, unmocked — /dashboard/forgot-password actually calls Supabase's live
/auth/v1/recover endpoint (using a fake, never-registered email so this
never spams a real inbox or trips the project's known email rate limit),
and /dashboard/reset-password is checked to actually embed the public
Supabase URL/anon key the page's client-side JS needs to complete the
recovery flow directly against Supabase (the access_token in the recovery
link lives in the URL fragment, which never reaches our server, so there is
no server-side "set new password" step to test here — Supabase's own API
handles that once the client-side JS calls it).
"""

from fastapi.testclient import TestClient

from src.orchestrator.config import get_settings
from src.orchestrator.main import app

client = TestClient(app)


def test_forgot_password_page_renders() -> None:
    resp = client.get("/dashboard/forgot-password")
    assert resp.status_code == 200
    assert 'name="email"' in resp.text
    assert "/dashboard/forgot-password" in resp.text


def test_forgot_password_submit_always_shows_generic_success() -> None:
    resp = client.post(
        "/dashboard/forgot-password",
        data={"email": "definitely-not-a-real-account-test@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "reset link has been sent" in resp.text.lower()


def test_reset_password_page_embeds_supabase_client_config() -> None:
    resp = client.get("/dashboard/reset-password")
    assert resp.status_code == 200
    settings = get_settings()
    assert settings.supabase_url in resp.text
    assert settings.supabase_anon_key in resp.text
    assert "access_token" in resp.text
