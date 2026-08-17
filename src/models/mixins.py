import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column


class TenantScopedMixin:
    """Every tenant-owned table carries tenant_id — this is what RLS policies key off."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
