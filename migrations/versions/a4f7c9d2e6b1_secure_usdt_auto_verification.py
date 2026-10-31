"""Secure automatic USDT verification claims and snapshots.

Revision ID: a4f7c9d2e6b1
Revises: 93c2e1ab7d4f
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4f7c9d2e6b1"
down_revision: str | Sequence[str] | None = "93c2e1ab7d4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "balance_topups",
        sa.Column("destination", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "balance_topups", sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "usdt_transaction_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network", sa.String(40), nullable=False),
        sa.Column("transaction_id", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("network", "transaction_id", name="uq_usdt_claim_network_tx"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_usdt_claim_source"),
    )


def downgrade() -> None:
    op.drop_table("usdt_transaction_claims")
    op.drop_column("balance_topups", "initiated_at")
    op.drop_column("balance_topups", "destination")
