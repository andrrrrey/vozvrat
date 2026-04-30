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
    # Map removed statuses to remaining ones
    op.execute("UPDATE refunds SET status = 'received' WHERE status = 'in_progress'")
    op.execute("UPDATE refunds SET status = 'approved' WHERE status IN ('sent_to_supplier', 'stock', 'completed')")

    # Recreate enum type with only 3 values
    op.execute("ALTER TYPE refundstatus RENAME TO refundstatus_old")
    op.execute("CREATE TYPE refundstatus AS ENUM ('received', 'approved', 'archive')")
    op.execute(
        "ALTER TABLE refunds ALTER COLUMN status TYPE refundstatus "
        "USING status::text::refundstatus"
    )
    op.execute("DROP TYPE refundstatus_old")


def downgrade() -> None:
    # Restore original enum
    op.execute("ALTER TYPE refundstatus RENAME TO refundstatus_new")
    op.execute(
        "CREATE TYPE refundstatus AS ENUM "
        "('received', 'in_progress', 'approved', 'sent_to_supplier', 'stock', 'completed', 'archive')"
    )
    op.execute(
        "ALTER TABLE refunds ALTER COLUMN status TYPE refundstatus "
        "USING status::text::refundstatus"
    )
    op.execute("DROP TYPE refundstatus_new")
