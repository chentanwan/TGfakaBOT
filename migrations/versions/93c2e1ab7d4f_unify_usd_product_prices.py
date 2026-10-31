"""Unify USDT and balance product prices.

Revision ID: 93c2e1ab7d4f
Revises: 81b972e6c4ad
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "93c2e1ab7d4f"
down_revision: str | Sequence[str] | None = "81b972e6c4ad"
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
            INSERT INTO product_prices (
                id, sku_id, provider, currency, amount_minor, is_active,
                metadata_json, created_at, updated_at
            )
            SELECT gen_random_uuid(), usdt.sku_id, 'balance', 'USD',
                   usdt.amount_minor, usdt.is_active, usdt.metadata_json, now(), now()
            FROM product_prices AS usdt
            WHERE usdt.provider = 'usdt'
              AND NOT EXISTS (
                  SELECT 1 FROM product_prices AS balance
                  WHERE balance.sku_id = usdt.sku_id
                    AND balance.provider = 'balance'
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE product_prices AS balance
            SET currency = 'USD',
                amount_minor = usdt.amount_minor,
                is_active = usdt.is_active,
                metadata_json = usdt.metadata_json,
                updated_at = now()
            FROM product_prices AS usdt
            WHERE balance.sku_id = usdt.sku_id
              AND balance.provider = 'balance'
              AND usdt.provider = 'usdt'
            """
        )
    )


def downgrade() -> None:
    pass
