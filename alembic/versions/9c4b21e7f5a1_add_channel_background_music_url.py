"""add channels.background_music_url

Revision ID: 9c4b21e7f5a1
Revises: 7b1e4c2f9a03
Create Date: 2026-08-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c4b21e7f5a1'
down_revision: Union[str, None] = '7b1e4c2f9a03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('channels', sa.Column('background_music_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('channels', 'background_music_url')
