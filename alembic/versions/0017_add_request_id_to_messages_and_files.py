"""Add request_id to messages and file_attachments; relax refund_id NOT NULL

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-24
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # messages
    op.add_column("messages", sa.Column("request_id", sa.Integer(), nullable=True))
    op.alter_column("messages", "refund_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(op.f("ix_messages_request_id"), "messages", ["request_id"], unique=False)
    op.create_foreign_key(
        "fk_messages_request_id_requests", "messages", "requests", ["request_id"], ["id"]
    )

    # file_attachments
    op.add_column("file_attachments", sa.Column("request_id", sa.Integer(), nullable=True))
    op.alter_column("file_attachments", "refund_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        op.f("ix_file_attachments_request_id"), "file_attachments", ["request_id"], unique=False
    )
    op.create_foreign_key(
        "fk_file_attachments_request_id_requests",
        "file_attachments",
        "requests",
        ["request_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_file_attachments_request_id_requests", "file_attachments", type_="foreignkey"
    )
    op.drop_index(op.f("ix_file_attachments_request_id"), table_name="file_attachments")
    op.alter_column("file_attachments", "refund_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("file_attachments", "request_id")

    op.drop_constraint("fk_messages_request_id_requests", "messages", type_="foreignkey")
    op.drop_index(op.f("ix_messages_request_id"), table_name="messages")
    op.alter_column("messages", "refund_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("messages", "request_id")
