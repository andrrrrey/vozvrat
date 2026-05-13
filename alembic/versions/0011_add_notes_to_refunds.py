"""Add notes field to refunds

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("refunds", sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("refunds", "notes")
