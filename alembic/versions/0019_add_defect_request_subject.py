"""Add 'defect' (Дефект детали) value to requestsubject enum

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-26
"""
from typing import Union
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on Postgres,
    # so use an autocommit block. IF NOT EXISTS makes the migration idempotent.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE requestsubject ADD VALUE IF NOT EXISTS 'defect'")


def downgrade() -> None:
    # Postgres does not support removing a value from an enum type; no-op.
    pass
