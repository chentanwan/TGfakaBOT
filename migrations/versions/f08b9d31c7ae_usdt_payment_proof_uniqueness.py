"""Scope payment proof uniqueness to payment provider.

Revision ID: f08b9d31c7ae
Revises: c74ad30f11b2
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f08b9d31c7ae"
down_revision: str | Sequence[str] | None = "c74ad30f11b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.drop_constraint(
        "uq_plugin_payments_transaction_id", "plugin_payments", type_="unique"
    )
    op.create_unique_constraint(
        "uq_plugin_payments_provider_transaction_id",
        "plugin_payments",
        ["provider", "transaction_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_plugin_payments_provider_transaction_id",
        "plugin_payments",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_plugin_payments_transaction_id", "plugin_payments", ["transaction_id"]
    )
