"""add message_id to file_attachments (photos attached to comments)

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-19
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_attachments",
        sa.Column("message_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_file_attachments_message_id"),
        "file_attachments",
        ["message_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_file_attachments_message_id_messages",
        "file_attachments",
        "messages",
        ["message_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_file_attachments_message_id_messages", "file_attachments", type_="foreignkey"
    )
    op.drop_index(op.f("ix_file_attachments_message_id"), table_name="file_attachments")
    op.drop_column("file_attachments", "message_id")
