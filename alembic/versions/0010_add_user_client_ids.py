"""Add user_client_ids table (multiple external IDs per user)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_client_ids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_user_client_id"),
    )
    op.create_index("ix_user_client_ids_id", "user_client_ids", ["id"])
    op.create_index("ix_user_client_ids_user_id", "user_client_ids", ["user_id"])
    op.create_index("ix_user_client_ids_client_id", "user_client_ids", ["client_id"])

    # Migrate existing external_id data
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO user_client_ids (user_id, client_id) "
        "SELECT id, external_id FROM users WHERE external_id IS NOT NULL"
    ))

    # Drop old external_id column
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_column("users", "external_id")


def downgrade():
    op.add_column("users", sa.Column("external_id", sa.String(100), nullable=True))
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)
    op.drop_index("ix_user_client_ids_client_id", table_name="user_client_ids")
    op.drop_index("ix_user_client_ids_user_id", table_name="user_client_ids")
    op.drop_index("ix_user_client_ids_id", table_name="user_client_ids")
    op.drop_table("user_client_ids")
