"""Add payment charge reconciliation audit fields.

Revision ID: c01ab8ca51d7
Revises: 913b686cd0fa
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c01ab8ca51d7"
down_revision: str | Sequence[str] | None = "913b686cd0fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.add_column(
        "payment_charges", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "payment_charges", sa.Column("resolved_by_telegram_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("payment_charges", sa.Column("resolution_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_charges", "resolution_note")
    op.drop_column("payment_charges", "resolved_by_telegram_id")
    op.drop_column("payment_charges", "resolved_at")
