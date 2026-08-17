import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.mixins import TenantScopedMixin, TimestampMixin


class ApiCallLog(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "api_call_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    stage: Mapped[str | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(nullable=False)
    endpoint: Mapped[str | None] = mapped_column(nullable=True)
    request_summary: Mapped[str | None] = mapped_column(nullable=True)
    response_summary: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    # YouTube Data API quota units (not dollars) — the platform-wide shared
    # resource from ARCHITECTURE.md §9. Only populated for provider='youtube_data_api'.
    quota_units: Mapped[int | None] = mapped_column(nullable=True)


class Approval(Base, TenantScopedMixin):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(nullable=False)
    requested_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(nullable=True)
    decision: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


class YoutubeVideo(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "youtube_videos"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    youtube_video_id: Mapped[str | None] = mapped_column(nullable=True)
    url: Mapped[str | None] = mapped_column(nullable=True)
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default="pending")


class AnalyticsSnapshot(Base, TenantScopedMixin):
    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    youtube_video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("youtube_videos.id"), nullable=False, index=True
    )
    snapshot_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default="{}")


class NotificationSent(Base, TenantScopedMixin):
    __tablename__ = "notifications_sent"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    notify_channel: Mapped[str] = mapped_column(nullable=False, server_default="whatsapp")
    message_type: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    sent_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
