import uuid

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.security import decrypt


class MissingProviderKeyError(Exception):
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"No API key connected for provider '{provider}'")


async def get_tenant_key(tenant_id: str | uuid.UUID, provider: str) -> str:
    """Workers run under service_session (RLS-bypassing, ARCHITECTURE.md §7) since
    a Celery task has no per-request auth context to switch into — the tenant_id
    passed in was already established when the job was created via a real
    tenant-scoped API call, so this is reading data the caller is entitled to,
    not an authorization decision being made here."""
    tid = uuid.UUID(str(tenant_id))
    async with service_session() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT encrypted_key FROM tenant_api_keys "
                        "WHERE tenant_id = :tenant_id AND provider = :provider"
                    ),
                    {"tenant_id": tid, "provider": provider},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise MissingProviderKeyError(provider)
    return decrypt(row["encrypted_key"])
