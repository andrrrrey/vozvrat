"""Add email_uid column to refunds table

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('refunds', sa.Column('email_uid', sa.String(100), nullable=True))
    op.create_index('ix_refunds_email_uid', 'refunds', ['email_uid'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_refunds_email_uid', table_name='refunds')
    op.drop_column('refunds', 'email_uid')
