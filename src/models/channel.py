import uuid
from typing import Any

from sqlalchemy import JSON, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.mixins import TenantScopedMixin, TimestampMixin


class Channel(Base, TenantScopedMixin, TimestampMixin):
    """A tenant's YouTube channel config — the 'configure once' unit from the main goal."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(nullable=False)
    niche: Mapped[str | None] = mapped_column(nullable=True)
    audience: Mapped[str | None] = mapped_column(nullable=True)
    language: Mapped[str] = mapped_column(nullable=False, server_default="en")
    video_length_target_seconds: Mapped[int | None] = mapped_column(nullable=True)
    style: Mapped[str | None] = mapped_column(nullable=True)
    posting_frequency: Mapped[str | None] = mapped_column(nullable=True)
    approval_gates: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
    youtube_channel_id: Mapped[str | None] = mapped_column(nullable=True)
    youtube_refresh_token_encrypted: Mapped[str | None] = mapped_column(nullable=True)
