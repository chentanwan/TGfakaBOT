"""Migrate active BEpusdt prices to neutral USDT pricing.

Revision ID: c74ad30f11b2
Revises: a61e4c82d017
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c74ad30f11b2"
down_revision: str | Sequence[str] | None = "a61e4c82d017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    bind.execute(
        sa.text(
            """
            UPDATE product_prices AS old
            SET provider = 'usdt'
            WHERE old.provider = 'bepusdt'
              AND NOT EXISTS (
                  SELECT 1 FROM product_prices AS current
                  WHERE current.sku_id = old.sku_id
                    AND current.provider = 'usdt'
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE product_prices
            SET is_active = false
            WHERE provider = 'bepusdt'
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE product_prices AS old
            SET provider = 'bepusdt'
            WHERE old.provider = 'usdt'
              AND NOT EXISTS (
                  SELECT 1 FROM product_prices AS current
                  WHERE current.sku_id = old.sku_id
                    AND current.provider = 'bepusdt'
              )
            """
        )
    )
