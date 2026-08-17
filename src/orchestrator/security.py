from cryptography.fernet import Fernet

from src.orchestrator.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def mask(value: str, visible: int = 4) -> str:
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"
