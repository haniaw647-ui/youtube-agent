import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.mixins import TenantScopedMixin, TimestampMixin

# Order matters — this is the fixed sequence from ARCHITECTURE.md §4 / DATA_FLOW.md.
# voice_over and visual_generation run in parallel (Celery group), joined before video_assembly.
PIPELINE_STAGES = [
    "topic_generation",
    "topic_scoring",
    "research",
    "script_writing",
    "script_qa",
    "voice_over",
    "visual_generation",
    "video_assembly",
    "subtitle_burn_in",
    "background_music",
    "thumbnail_generation",
    "metadata_generation",
    "final_qa",
    "youtube_upload",
    "whatsapp_notification",
]

APPROVABLE_STAGES = {"topic_scoring", "script_qa", "final_qa"}


class Job(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    current_stage: Mapped[str] = mapped_column(nullable=False)
    overall_status: Mapped[str] = mapped_column(nullable=False, server_default="running")
    title: Mapped[str | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class JobStage(Base, TenantScopedMixin):
    __tablename__ = "job_stages"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    retry_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(nullable=True)
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, server_default="0")


class Topic(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    title: Mapped[str] = mapped_column(nullable=False)
    hook: Mapped[str | None] = mapped_column(nullable=True)
    angle: Mapped[str | None] = mapped_column(nullable=True)
    audience: Mapped[str | None] = mapped_column(nullable=True)
    estimated_interest: Mapped[int | None] = mapped_column(nullable=True)
    uniqueness_score: Mapped[int | None] = mapped_column(nullable=True)
    difficulty: Mapped[int | None] = mapped_column(nullable=True)
    evergreen: Mapped[bool | None] = mapped_column(nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default="candidate")


class Script(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, server_default="1")
    content: Mapped[str] = mapped_column(nullable=False)
    word_count: Mapped[int | None] = mapped_column(nullable=True)
    est_duration_seconds: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default="draft")


class Asset(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(nullable=False)
    segment_index: Mapped[int | None] = mapped_column(nullable=True)
    storage_path: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str | None] = mapped_column(nullable=True)
    license_type: Mapped[str | None] = mapped_column(nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, server_default="0")
