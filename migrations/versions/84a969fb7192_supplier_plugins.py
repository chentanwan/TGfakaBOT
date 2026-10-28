"""Add generic supplier plugin support.

Revision ID: 84a969fb7192
Revises: 4d5c3be21a7f
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "84a969fb7192"
down_revision: str | Sequence[str] | None = "4d5c3be21a7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("_tgfakabot_schema_bootstrap"):
        return
    op.add_column(
        "skus",
        sa.Column("stars_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column("skus", "stars_enabled", server_default=None)
    op.create_table(
        "supplier_products",
        sa.Column("sku_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("external_product_id", sa.String(length=255), nullable=False),
        sa.Column("cost_currency", sa.String(length=12), nullable=False),
        sa.Column("cost_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        sa.Column("requires_customer_email", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name=op.f("fk_supplier_products_sku_id_skus")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_products")),
        sa.UniqueConstraint(
            "provider",
            "external_product_id",
            name=op.f("uq_supplier_products_provider"),
        ),
        sa.UniqueConstraint("sku_id", "provider", name=op.f("uq_supplier_products_sku_id")),
    )
    op.create_table(
        "supplier_orders",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("external_order_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cost_currency", sa.String(length=12), nullable=False),
        sa.Column("cost_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("response_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_supplier_orders_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_orders")),
        sa.UniqueConstraint("order_id", name=op.f("uq_supplier_orders_order_id")),
        sa.UniqueConstraint(
            "provider", "external_order_id", name=op.f("uq_supplier_orders_provider")
        ),
    )
    op.create_index(
        "ix_supplier_orders_provider_status",
        "supplier_orders",
        ["provider", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_supplier_orders_provider_status", table_name="supplier_orders")
    op.drop_table("supplier_orders")
    op.drop_table("supplier_products")
    op.drop_column("skus", "stars_enabled")
