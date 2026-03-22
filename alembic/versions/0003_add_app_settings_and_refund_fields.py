"""Add app_settings table and refund supplier fields

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('key')
    )

    op.add_column('refunds', sa.Column('supplier_doc_number', sa.String(length=100), nullable=True))
    op.add_column('refunds', sa.Column('supplier_email_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('refunds', 'supplier_email_sent_at')
    op.drop_column('refunds', 'supplier_doc_number')
    op.drop_table('app_settings')
