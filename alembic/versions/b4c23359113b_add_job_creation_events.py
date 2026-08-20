"""add job creation events

Revision ID: b4c23359113b
Revises: b3f8d2a1c7e4
Create Date: 2026-08-20 17:42:43.527167

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c23359113b'
down_revision: Union[str, None] = 'b3f8d2a1c7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Append-only — deliberately no FK to jobs.id. The dashboard's "Activity"
    # graph used to COUNT(*) the live `jobs` table grouped by created_at, so
    # deleting a job (a real feature, not a bug) silently erased that day's
    # history from the graph too. This table records "a job was created" once,
    # at creation time, and is never touched by job deletion — job_id is kept
    # only for debugging traceability, not as an enforced reference.
    op.create_table(
        "job_creation_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_creation_events_tenant_id", "job_creation_events", ["tenant_id"]
    )
    op.create_index(
        "ix_job_creation_events_created_at", "job_creation_events", ["created_at"]
    )

    op.execute("ALTER TABLE job_creation_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE job_creation_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON job_creation_events "
        "USING (tenant_id = auth.uid()) WITH CHECK (tenant_id = auth.uid());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE job_creation_events TO authenticated;"
    )


def downgrade() -> None:
    op.drop_table("job_creation_events")
