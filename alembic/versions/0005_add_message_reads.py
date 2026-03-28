"""Add message_reads table for unread tracking

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'message_reads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('read_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'user_id', name='uq_message_read'),
    )
    op.create_index('ix_message_reads_id', 'message_reads', ['id'], unique=False)
    op.create_index('ix_message_reads_message_id', 'message_reads', ['message_id'], unique=False)
    op.create_index('ix_message_reads_user_id', 'message_reads', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_message_reads_user_id', table_name='message_reads')
    op.drop_index('ix_message_reads_message_id', table_name='message_reads')
    op.drop_index('ix_message_reads_id', table_name='message_reads')
    op.drop_table('message_reads')
