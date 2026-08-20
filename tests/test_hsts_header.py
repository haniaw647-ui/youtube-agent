"""Confirmed live via Qualys SSL Labs: the deployed domain's TLS
configuration (certificate, protocol support, cipher strength, key
exchange) was already grade A with nothing left to tune — that layer is
Railway's edge, not something this app configures. Strict-Transport-
Security was the one specific, documented gap between A and A+."""

from fastapi.testclient import TestClient

from src.orchestrator.main import app

client = TestClient(app)


def test_every_response_carries_a_strong_hsts_header():
    resp = client.get("/health")
    assert resp.status_code == 200
    hsts = resp.headers.get("strict-transport-security")
    assert hsts is not None
    assert "max-age=63072000" in hsts
    assert "includeSubDomains" in hsts
    assert "preload" in hsts


def test_hsts_header_present_even_on_a_404():
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
    assert resp.headers.get("strict-transport-security") is not None
