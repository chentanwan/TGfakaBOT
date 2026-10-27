"""Add supplier recovery audit and profit suspension.

Revision ID: f6230ee498d2
Revises: 84a969fb7192
Create Date: 2026-10-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6230ee498d2"
down_revision: str | Sequence[str] | None = "84a969fb7192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "supplier_products",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "supplier_products",
        sa.Column(
            "suspended_for_profit", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "supplier_products", sa.Column("suspension_reason", sa.Text(), nullable=True)
    )
    op.alter_column("supplier_products", "suspended_for_profit", server_default=None)
    op.add_column("supplier_orders", sa.Column("recovery_note", sa.Text(), nullable=True))
    op.add_column(
        "supplier_orders",
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "supplier_orders",
        sa.Column("recovered_by_telegram_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_orders", "recovered_by_telegram_id")
    op.drop_column("supplier_orders", "recovered_at")
    op.drop_column("supplier_orders", "recovery_note")
    op.drop_column("supplier_products", "suspension_reason")
    op.drop_column("supplier_products", "suspended_for_profit")
    op.drop_column("supplier_products", "last_synced_at")
