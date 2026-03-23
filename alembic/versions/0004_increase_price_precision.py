"""Increase price precision in refund_items

Revision ID: 0004
Revises: 0003
Create Date: 2024-01-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'refund_items',
        'price',
        type_=sa.Numeric(16, 2),
        existing_type=sa.Numeric(10, 2),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'refund_items',
        'price',
        type_=sa.Numeric(10, 2),
        existing_type=sa.Numeric(16, 2),
        existing_nullable=False,
    )
