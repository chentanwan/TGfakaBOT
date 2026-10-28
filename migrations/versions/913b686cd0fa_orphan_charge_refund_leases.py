"""Add orphan charge audit fields and refund leases.

Revision ID: 913b686cd0fa
Revises: 8f6b71b67f0a
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "913b686cd0fa"
down_revision: str | Sequence[str] | None = "8f6b71b67f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.add_column("payment_charges", sa.Column("related_order_id", sa.Uuid(), nullable=True))
    op.add_column("payment_charges", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "payment_charges",
        sa.Column("refund_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "payment_charges",
        sa.Column("refund_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payment_charges",
        sa.Column("refund_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_payment_charges_related_order",
        "payment_charges",
        "orders",
        ["related_order_id"],
        ["id"],
    )
    op.drop_constraint("payment_status", "payment_charges", type_="check")
    op.execute(
        "UPDATE payment_charges SET status = 'refund_unknown' WHERE status = 'refund_pending'"
    )
    op.create_check_constraint(
        "payment_status",
        "payment_charges",
        "status IN ('succeeded','refund_pending','refunded','refund_failed','refund_unknown')",
    )
    op.create_index(
        "ix_payment_charges_resolution",
        "payment_charges",
        ["resolution_status", "received_at"],
    )


def downgrade() -> None:
    op.drop_constraint("payment_status", "payment_charges", type_="check")
    op.execute(
        "UPDATE payment_charges SET status = 'refund_failed' WHERE status = 'refund_unknown'"
    )
    op.create_check_constraint(
        "payment_status",
        "payment_charges",
        "status IN ('succeeded','refund_pending','refunded','refund_failed')",
    )
    op.drop_index("ix_payment_charges_resolution", table_name="payment_charges")
    op.drop_constraint("fk_payment_charges_related_order", "payment_charges", type_="foreignkey")
    op.drop_column("payment_charges", "refund_lease_expires_at")
    op.drop_column("payment_charges", "refund_claimed_at")
    op.drop_column("payment_charges", "refund_claim_token")
    op.drop_column("payment_charges", "telegram_user_id")
    op.drop_column("payment_charges", "related_order_id")
