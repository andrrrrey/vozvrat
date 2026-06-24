"""Add upd_number field to refunds

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("refunds", sa.Column("upd_number", sa.String(length=100), nullable=True))


def downgrade():
    op.drop_column("refunds", "upd_number")
