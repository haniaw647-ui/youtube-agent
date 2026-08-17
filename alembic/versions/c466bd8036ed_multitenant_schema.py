"""multitenant schema

Revision ID: c466bd8036ed
Revises: b4dcccab09fd
Create Date: 2026-08-17 15:31:32.159939

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c466bd8036ed"
down_revision: Union[str, None] = "b4dcccab09fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that carry tenant_id and get an RLS policy tying rows to auth.uid().
# `tenants` itself uses `id = auth.uid()` instead (handled separately below).
TENANT_SCOPED_TABLES = [
    "channels",
    "jobs",
    "job_stages",
    "topics",
    "scripts",
    "assets",
    "api_call_logs",
    "approvals",
    "youtube_videos",
    "analytics_snapshots",
    "notifications_sent",
    "tenant_api_keys",
]


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "channels",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("niche", sa.String(), nullable=True),
        sa.Column("audience", sa.String(), nullable=True),
        sa.Column("language", sa.String(), server_default="en", nullable=False),
        sa.Column("video_length_target_seconds", sa.Integer(), nullable=True),
        sa.Column("style", sa.String(), nullable=True),
        sa.Column("posting_frequency", sa.String(), nullable=True),
        sa.Column("approval_gates", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("provider_config", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("youtube_channel_id", sa.String(), nullable=True),
        sa.Column("youtube_refresh_token_encrypted", sa.String(), nullable=True),
        sa.Column("whatsapp_recipient_number", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channels_tenant_id", "channels", ["tenant_id"])

    # jobs.topic_id -> topics.id and topics.job_id -> jobs.id form a cycle;
    # jobs is created first without that FK, which is added after topics exists.
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("current_stage", sa.String(), nullable=False),
        sa.Column("overall_status", sa.String(), server_default="running", nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_channel_id", "jobs", ["channel_id"])
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])

    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("hook", sa.String(), nullable=True),
        sa.Column("angle", sa.String(), nullable=True),
        sa.Column("audience", sa.String(), nullable=True),
        sa.Column("estimated_interest", sa.Integer(), nullable=True),
        sa.Column("uniqueness_score", sa.Integer(), nullable=True),
        sa.Column("difficulty", sa.Integer(), nullable=True),
        sa.Column("evergreen", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("status", sa.String(), server_default="candidate", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topics_channel_id", "topics", ["channel_id"])
    op.create_index("ix_topics_tenant_id", "topics", ["tenant_id"])

    op.create_foreign_key(
        "fk_jobs_topic_id_topics", "jobs", "topics", ["topic_id"], ["id"]
    )

    op.create_table(
        "scripts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("est_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scripts_job_id", "scripts", ["job_id"])
    op.create_index("ix_scripts_tenant_id", "scripts", ["tenant_id"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("license_type", sa.String(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_job_id", "assets", ["job_id"])
    op.create_index("ix_assets_tenant_id", "assets", ["tenant_id"])

    op.create_table(
        "job_stages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("output_ref", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_stages_job_id", "job_stages", ["job_id"])
    op.create_index("ix_job_stages_tenant_id", "job_stages", ["tenant_id"])

    op.create_table(
        "api_call_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("request_summary", sa.String(), nullable=True),
        sa.Column("response_summary", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=4), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_call_logs_job_id", "api_call_logs", ["job_id"])
    op.create_index("ix_api_call_logs_tenant_id", "api_call_logs", ["tenant_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_job_id", "approvals", ["job_id"])
    op.create_index("ix_approvals_tenant_id", "approvals", ["tenant_id"])

    op.create_table(
        "youtube_videos",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("youtube_video_id", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("scheduled_publish_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_youtube_videos_channel_id", "youtube_videos", ["channel_id"])
    op.create_index("ix_youtube_videos_job_id", "youtube_videos", ["job_id"])
    op.create_index("ix_youtube_videos_tenant_id", "youtube_videos", ["tenant_id"])

    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("youtube_video_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("metrics", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["youtube_video_id"], ["youtube_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_snapshots_tenant_id", "analytics_snapshots", ["tenant_id"])
    op.create_index(
        "ix_analytics_snapshots_youtube_video_id", "analytics_snapshots", ["youtube_video_id"]
    )

    op.create_table(
        "notifications_sent",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("notify_channel", sa.String(), server_default="whatsapp", nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_sent_job_id", "notifications_sent", ["job_id"])
    op.create_index("ix_notifications_sent_tenant_id", "notifications_sent", ["tenant_id"])

    op.create_table(
        "tenant_api_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("encrypted_key", sa.String(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), server_default="unvalidated", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_api_keys_tenant_provider"),
    )
    op.create_index("ix_tenant_api_keys_tenant_id", "tenant_api_keys", ["tenant_id"])

    # --- Row-Level Security ---------------------------------------------------
    # The app connects as the `authenticated` Postgres role for tenant-scoped
    # requests (never as the `postgres` superuser, which bypasses RLS), after
    # SET LOCAL request.jwt.claims so auth.uid() resolves to the caller's tenant.
    # `postgres` (used for migrations and internal/worker access) always bypasses
    # RLS regardless of these policies — that is the intended service-role path.
    op.execute("GRANT USAGE ON SCHEMA public TO authenticated;")

    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_self_access ON tenants "
        "USING (id = auth.uid()) WITH CHECK (id = auth.uid());"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tenants TO authenticated;")

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = auth.uid()) WITH CHECK (tenant_id = auth.uid());"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO authenticated;")


def downgrade() -> None:
    op.drop_table("tenant_api_keys")
    op.drop_table("notifications_sent")
    op.drop_table("analytics_snapshots")
    op.drop_table("youtube_videos")
    op.drop_table("approvals")
    op.drop_table("api_call_logs")
    op.drop_table("job_stages")
    op.drop_table("assets")
    op.drop_table("scripts")
    op.drop_constraint("fk_jobs_topic_id_topics", "jobs", type_="foreignkey")
    op.drop_table("topics")
    op.drop_table("jobs")
    op.drop_table("channels")
    op.drop_table("tenants")
