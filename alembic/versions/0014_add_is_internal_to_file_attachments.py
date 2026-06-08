"""add is_internal flag to file_attachments

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-08

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_attachments",
        sa.Column(
            "is_internal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("file_attachments", "is_internal")
