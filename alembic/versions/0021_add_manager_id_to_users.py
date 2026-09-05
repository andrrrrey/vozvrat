"""Add manager_id to users (client -> managing staff)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("manager_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_manager_id_users",
        "users", "users",
        ["manager_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_manager_id", "users", ["manager_id"])


def downgrade():
    op.drop_index("ix_users_manager_id", table_name="users")
    op.drop_constraint("fk_users_manager_id_users", "users", type_="foreignkey")
    op.drop_column("users", "manager_id")
