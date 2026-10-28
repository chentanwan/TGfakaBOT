"""Payment safety state and refund audit fields.

Revision ID: 8f6b71b67f0a
Revises: bb4a86adf422
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f6b71b67f0a"
down_revision: str | Sequence[str] | None = "bb4a86adf422"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.drop_constraint("order_status", "orders", type_="check")
    op.create_check_constraint(
        "order_status",
        "orders",
        "status IN ('pending_payment','payment_processing','paid','fulfilled','expired',"
        "'payment_exception','refund_pending','refunded')",
    )
    op.add_column("orders", sa.Column("payment_exception_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("resolved_by_telegram_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("resolution_action", sa.String(length=40), nullable=True))
    op.create_index(
        "uq_cards_order_item",
        "cards",
        ["order_item_id"],
        unique=True,
        postgresql_where=sa.text("order_item_id IS NOT NULL"),
    )
    op.add_column("payment_charges", sa.Column("refund_requested_at", sa.DateTime(timezone=True)))
    op.add_column(
        "payment_charges", sa.Column("refund_last_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "payment_charges",
        sa.Column("refund_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("payment_charges", sa.Column("refund_last_error", sa.Text()))
    op.add_column("payment_charges", sa.Column("refund_requested_by_telegram_id", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("payment_charges", "refund_requested_by_telegram_id")
    op.drop_column("payment_charges", "refund_last_error")
    op.drop_column("payment_charges", "refund_attempts")
    op.drop_column("payment_charges", "refund_last_attempt_at")
    op.drop_column("payment_charges", "refund_requested_at")
    op.drop_index("uq_cards_order_item", table_name="cards")
    op.drop_column("orders", "resolution_action")
    op.drop_column("orders", "resolved_by_telegram_id")
    op.drop_column("orders", "resolved_at")
    op.drop_column("orders", "payment_exception_reason")
    op.drop_constraint("order_status", "orders", type_="check")
    op.create_check_constraint(
        "order_status",
        "orders",
        "status IN ('pending_payment','paid','fulfilled','expired','payment_exception',"
        "'refund_pending','refunded')",
    )
