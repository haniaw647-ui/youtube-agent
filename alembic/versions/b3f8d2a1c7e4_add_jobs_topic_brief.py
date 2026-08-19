"""add jobs.topic_brief

Revision ID: b3f8d2a1c7e4
Revises: 9c4b21e7f5a1
Create Date: 2026-08-19 15:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f8d2a1c7e4'
down_revision: Union[str, None] = '9c4b21e7f5a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('topic_brief', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'topic_brief')
