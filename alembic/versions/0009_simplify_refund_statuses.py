"""simplify refund statuses to received, approved, archive

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-30

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Step 1: migrate data (DML — runs inside the transaction)
    bind.execute(sa.text(
        "UPDATE refunds SET status = 'received' WHERE status = 'in_progress'"
    ))
    bind.execute(sa.text(
        "UPDATE refunds SET status = 'approved' "
        "WHERE status IN ('sent_to_supplier', 'stock', 'completed')"
    ))

    # Step 2: recreate the PostgreSQL enum type.
    # DDL on enum types must run outside an explicit transaction block.
    bind.execute(sa.text("COMMIT"))
    bind.execute(sa.text("ALTER TYPE refundstatus RENAME TO refundstatus_old"))
    bind.execute(sa.text(
        "CREATE TYPE refundstatus AS ENUM ('received', 'approved', 'archive')"
    ))
    bind.execute(sa.text(
        "ALTER TABLE refunds ALTER COLUMN status TYPE refundstatus "
        "USING status::text::refundstatus"
    ))
    bind.execute(sa.text("DROP TYPE refundstatus_old"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("COMMIT"))
    bind.execute(sa.text("ALTER TYPE refundstatus RENAME TO refundstatus_new"))
    bind.execute(sa.text(
        "CREATE TYPE refundstatus AS ENUM "
        "('received', 'in_progress', 'approved', 'sent_to_supplier', 'stock', 'completed', 'archive')"
    ))
    bind.execute(sa.text(
        "ALTER TABLE refunds ALTER COLUMN status TYPE refundstatus "
        "USING status::text::refundstatus"
    ))
    bind.execute(sa.text("DROP TYPE refundstatus_new"))
