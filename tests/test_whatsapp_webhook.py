import hashlib
import hmac

from src.orchestrator.whatsapp_webhook import verify_signature

APP_SECRET = "test-app-secret"


def _sign(payload: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_correctly_signed_payload():
    payload = b'{"entry": []}'
    signature = _sign(payload)

    assert verify_signature(payload, signature, APP_SECRET) is True


def test_verify_signature_rejects_wrong_secret():
    payload = b'{"entry": []}'
    signature = _sign(payload, secret="wrong-secret")

    assert verify_signature(payload, signature, APP_SECRET) is False


def test_verify_signature_rejects_tampered_payload():
    original_payload = b'{"entry": []}'
    signature = _sign(original_payload)
    tampered_payload = b'{"entry": ["injected"]}'

    assert verify_signature(tampered_payload, signature, APP_SECRET) is False


def test_verify_signature_rejects_missing_header():
    assert verify_signature(b"data", "", APP_SECRET) is False


def test_verify_signature_rejects_malformed_header():
    assert verify_signature(b"data", "not-a-sha256-header", APP_SECRET) is False
