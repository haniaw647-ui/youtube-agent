import uuid

from sqlalchemy import text

from src.orchestrator.db import tenant_session


async def ensure_tenant(tenant_id: uuid.UUID, display_name: str) -> None:
    """Idempotent: creates the tenant's own row if this is their first time being
    seen (e.g. right after signup, or first login after email confirmation).
    Runs in that tenant's own RLS-scoped session — a tenant can only ever create
    or touch its own row, enforced by the `id = auth.uid()` policy on `tenants`."""
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO tenants (id, display_name) VALUES (:id, :display_name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "display_name": display_name},
        )
