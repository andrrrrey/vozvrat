"""Add processing_status and skip_reason to mail_notifications

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mail_notifications",
        sa.Column("processing_status", sa.String(20), nullable=True),
    )
    op.add_column(
        "mail_notifications",
        sa.Column("skip_reason", sa.String(500), nullable=True),
    )


def downgrade():
    op.drop_column("mail_notifications", "skip_reason")
    op.drop_column("mail_notifications", "processing_status")
