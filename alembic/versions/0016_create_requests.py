"""Create requests and request_items tables

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-24
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_id", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum("received", "in_work", "completed", name="requeststatus"),
            nullable=False,
        ),
        sa.Column("client_name", sa.String(length=255), nullable=False),
        sa.Column("client_user_id", sa.Integer(), nullable=True),
        sa.Column("executor_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["client_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["executor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requests_id"), "requests", ["id"], unique=False)
    op.create_index(op.f("ix_requests_display_id"), "requests", ["display_id"], unique=True)
    op.create_index(op.f("ix_requests_executor_id"), "requests", ["executor_id"], unique=False)

    op.create_table(
        "request_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("article", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(16, 2), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("position_id", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_request_items_id"), "request_items", ["id"], unique=False)
    op.create_index(op.f("ix_request_items_request_id"), "request_items", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_request_items_request_id"), table_name="request_items")
    op.drop_index(op.f("ix_request_items_id"), table_name="request_items")
    op.drop_table("request_items")
    op.drop_index(op.f("ix_requests_executor_id"), table_name="requests")
    op.drop_index(op.f("ix_requests_display_id"), table_name="requests")
    op.drop_index(op.f("ix_requests_id"), table_name="requests")
    op.drop_table("requests")
    sa.Enum(name="requeststatus").drop(op.get_bind(), checkfirst=True)
