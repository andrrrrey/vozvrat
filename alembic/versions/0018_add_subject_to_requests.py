"""Add subject (тема) to requests

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-26
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    subject_enum = sa.Enum(
        "delivery", "rejection", "site", "free", name="requestsubject"
    )
    subject_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "requests",
        sa.Column("subject", subject_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requests", "subject")
    sa.Enum(name="requestsubject").drop(op.get_bind(), checkfirst=True)
