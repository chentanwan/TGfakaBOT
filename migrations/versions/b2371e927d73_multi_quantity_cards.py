"""Allow multiple delivered cards per order item.

Revision ID: b2371e927d73
Revises: 04d1bf7f901a
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2371e927d73"
down_revision: str | Sequence[str] | None = "04d1bf7f901a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("_tgfakabot_schema_bootstrap"):
        return
    op.drop_index("uq_cards_order_item", table_name="cards")
    op.create_index("ix_cards_order_item", "cards", ["order_item_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cards_order_item", table_name="cards")
    op.create_index(
        "uq_cards_order_item",
        "cards",
        ["order_item_id"],
        unique=True,
        postgresql_where="order_item_id IS NOT NULL",
    )
