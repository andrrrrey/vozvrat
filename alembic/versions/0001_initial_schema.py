"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('role', sa.Enum('admin', 'staff', 'client', name='userrole'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # suppliers table
    op.create_table(
        'suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_suppliers_id'), 'suppliers', ['id'], unique=False)

    # refunds table
    op.create_table(
        'refunds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('display_id', sa.String(length=20), nullable=False),
        sa.Column('status', sa.Enum('received', 'in_progress', 'approved', 'sent_to_supplier', 'stock', 'completed', 'archive', name='refundstatus'), nullable=False),
        sa.Column('source', sa.Enum('manual', 'email', name='refundsource'), nullable=False),
        sa.Column('client_name', sa.String(length=255), nullable=False),
        sa.Column('client_user_id', sa.Integer(), nullable=True),
        sa.Column('supplier_id', sa.Integer(), nullable=True),
        sa.Column('order_id', sa.String(length=100), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('email_subject', sa.String(length=500), nullable=True),
        sa.Column('email_from', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['client_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refunds_id'), 'refunds', ['id'], unique=False)
    op.create_index(op.f('ix_refunds_display_id'), 'refunds', ['display_id'], unique=True)

    # refund_items table
    op.create_table(
        'refund_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('refund_id', sa.Integer(), nullable=False),
        sa.Column('article', sa.String(length=255), nullable=False),
        sa.Column('brand', sa.String(length=255), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['refund_id'], ['refunds.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refund_items_id'), 'refund_items', ['id'], unique=False)
    op.create_index(op.f('ix_refund_items_refund_id'), 'refund_items', ['refund_id'], unique=False)

    # file_attachments table
    op.create_table(
        'file_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('refund_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=500), nullable=False),
        sa.Column('stored_path', sa.String(length=1000), nullable=False),
        sa.Column('file_type', sa.Enum('xls', 'photo', 'pdf_ukd', 'other', name='filetype'), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['refund_id'], ['refunds.id']),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_file_attachments_id'), 'file_attachments', ['id'], unique=False)
    op.create_index(op.f('ix_file_attachments_refund_id'), 'file_attachments', ['refund_id'], unique=False)

    # messages table
    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('refund_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('visibility', sa.Enum('all', 'staff_only', name='messagevisibility'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['refund_id'], ['refunds.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_id'), 'messages', ['id'], unique=False)
    op.create_index(op.f('ix_messages_refund_id'), 'messages', ['refund_id'], unique=False)


def downgrade() -> None:
    op.drop_table('messages')
    op.drop_table('file_attachments')
    op.drop_table('refund_items')
    op.drop_table('refunds')
    op.drop_table('suppliers')
    op.drop_table('users')

    op.execute("DROP TYPE IF EXISTS messagevisibility")
    op.execute("DROP TYPE IF EXISTS filetype")
    op.execute("DROP TYPE IF EXISTS refundsource")
    op.execute("DROP TYPE IF EXISTS refundstatus")
    op.execute("DROP TYPE IF EXISTS userrole")
