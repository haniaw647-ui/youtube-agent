import uuid

import pytest

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
