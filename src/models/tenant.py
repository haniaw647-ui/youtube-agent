import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.mixins import TimestampMixin


class Tenant(Base, TimestampMixin):
    """One row per YouTuber/customer account. id matches the Supabase auth.users id 1:1 —
    this is what makes RLS policies a plain `tenant_id = auth.uid()` comparison."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default="active")


class TenantApiKey(Base, TimestampMixin):
    """Each tenant's own BYO provider key (Anthropic, ElevenLabs, ...), encrypted at rest."""

    __tablename__ = "tenant_api_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_api_keys_tenant_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(nullable=False)
    encrypted_key: Mapped[str] = mapped_column(nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default="unvalidated")
