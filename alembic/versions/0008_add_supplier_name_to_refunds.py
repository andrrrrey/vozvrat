"""add supplier_name to refunds

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-23

"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('refunds', sa.Column('supplier_name', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('refunds', 'supplier_name')
