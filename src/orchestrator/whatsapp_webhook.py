import hashlib
import hmac


def verify_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """Meta signs every webhook POST with X-Hub-Signature-256: sha256=<hex>.
    Without this check, anyone who finds the webhook URL could POST forged
    delivery-status updates."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
