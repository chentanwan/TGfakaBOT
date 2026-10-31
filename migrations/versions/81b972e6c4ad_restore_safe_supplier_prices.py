"""Restore safe supplier USD purchase prices.

Revision ID: 81b972e6c4ad
Revises: 6f3d8c21a4b7
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "81b972e6c4ad"
down_revision: str | Sequence[str] | None = "6f3d8c21a4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    bind.execute(sa.text("ALTER TABLE users ALTER COLUMN language_code SET DEFAULT 'zh'"))
    bind.execute(
        sa.text(
            """
            UPDATE product_prices AS price
            SET is_active = true,
                metadata_json = jsonb_set(
                    COALESCE(price.metadata_json, '{}'::jsonb),
                    '{pricing}',
                    '"auto_markup"'::jsonb,
                    true
                )
            FROM supplier_products AS supplier
            WHERE supplier.sku_id = price.sku_id
              AND supplier.provider = 'prodseller'
              AND price.provider = 'usdt'
              AND price.currency = 'USD'
              AND COALESCE(price.metadata_json->>'source', '') = 'prodseller'
              AND NOT (COALESCE(price.metadata_json, '{}'::jsonb) ? 'pricing')
              AND price.amount_minor >= COALESCE(
                    NULLIF(price.metadata_json->>'minimum_safe_minor', '')::bigint,
                    supplier.cost_amount_minor
                  )
              AND supplier.in_stock = true
              AND supplier.suspended_for_profit = false
              AND supplier.requires_customer_email = false
            """
        )
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN language_code SET DEFAULT 'en'")
