"""initial empty migration

Revision ID: b4dcccab09fd
Revises: 
Create Date: 2026-08-17 14:38:05.563766

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4dcccab09fd'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
