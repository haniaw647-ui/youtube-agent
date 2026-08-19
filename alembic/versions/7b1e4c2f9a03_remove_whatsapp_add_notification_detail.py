"""remove whatsapp_recipient_number, add notifications_sent.detail

Revision ID: 7b1e4c2f9a03
Revises: 02180c11c28d
Create Date: 2026-08-18 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7b1e4c2f9a03'
down_revision: Union[str, None] = '02180c11c28d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('channels', 'whatsapp_recipient_number')
    op.add_column('notifications_sent', sa.Column('detail', sa.String(), nullable=True))
    op.alter_column('notifications_sent', 'notify_channel', server_default='in_app')


def downgrade() -> None:
    op.alter_column('notifications_sent', 'notify_channel', server_default='whatsapp')
    op.drop_column('notifications_sent', 'detail')
    op.add_column('channels', sa.Column('whatsapp_recipient_number', sa.String(), nullable=True))
