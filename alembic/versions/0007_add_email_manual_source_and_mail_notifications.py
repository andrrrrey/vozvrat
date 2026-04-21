"""add email_manual source and mail_notifications

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-21

"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE refundsource ADD VALUE IF NOT EXISTS 'email_manual'")

    op.add_column('users', sa.Column('external_id', sa.String(100), nullable=True))
    op.create_index('ix_users_external_id', 'users', ['external_id'], unique=True)

    op.create_table(
        'mail_notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_uid', sa.String(100), nullable=False),
        sa.Column('subject', sa.String(500), nullable=True),
        sa.Column('from_email', sa.String(255), nullable=True),
        sa.Column('from_name', sa.String(255), nullable=True),
        sa.Column('refund_id', sa.Integer(), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['refund_id'], ['refunds.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mail_notifications_id', 'mail_notifications', ['id'], unique=False)
    op.create_index('ix_mail_notifications_email_uid', 'mail_notifications', ['email_uid'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_mail_notifications_email_uid', table_name='mail_notifications')
    op.drop_index('ix_mail_notifications_id', table_name='mail_notifications')
    op.drop_table('mail_notifications')
    op.drop_index('ix_users_external_id', table_name='users')
    op.drop_column('users', 'external_id')
