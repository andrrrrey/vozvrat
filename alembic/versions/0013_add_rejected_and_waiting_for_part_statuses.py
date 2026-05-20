"""add rejected and waiting_for_part refund statuses

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-20

"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # ADD VALUE must run outside an explicit transaction on older PG versions
    bind.execute(sa.text("COMMIT"))
    bind.execute(sa.text("ALTER TYPE refundstatus ADD VALUE IF NOT EXISTS 'rejected'"))
    bind.execute(sa.text("ALTER TYPE refundstatus ADD VALUE IF NOT EXISTS 'waiting_for_part'"))


def downgrade() -> None:
    bind = op.get_bind()

    # Migrate rows with new statuses back to 'received'
    bind.execute(sa.text(
        "UPDATE refunds SET status = 'received' "
        "WHERE status IN ('rejected', 'waiting_for_part')"
    ))

    # Recreate the enum without the new values
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
