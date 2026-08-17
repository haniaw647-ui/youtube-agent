"""add job id sequence

Revision ID: 1cbd114abc00
Revises: c466bd8036ed
Create Date: 2026-08-17 15:38:14.468382

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1cbd114abc00'
down_revision: Union[str, None] = 'c466bd8036ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backs the job_YYYY_NNNNN id format. authenticated needs USAGE to call
    # nextval() when a tenant-scoped session creates a job directly (not just
    # via service-role worker code).
    op.execute("CREATE SEQUENCE job_id_seq;")
    op.execute("GRANT USAGE ON SEQUENCE job_id_seq TO authenticated;")


def downgrade() -> None:
    op.execute("DROP SEQUENCE job_id_seq;")
