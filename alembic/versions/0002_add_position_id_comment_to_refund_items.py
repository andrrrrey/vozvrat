"""Add position_id and comment to refund_items

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('refund_items', sa.Column('position_id', sa.String(length=255), nullable=True))
    op.add_column('refund_items', sa.Column('comment', sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column('refund_items', 'comment')
    op.drop_column('refund_items', 'position_id')
