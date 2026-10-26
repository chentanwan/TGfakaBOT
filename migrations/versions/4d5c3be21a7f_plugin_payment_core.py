"""Add generic plugin payment core.

Revision ID: 4d5c3be21a7f
Revises: c01ab8ca51d7
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4d5c3be21a7f"
down_revision: str | Sequence[str] | None = "c01ab8ca51d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_prices",
        sa.Column("sku_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "amount_minor >= 1", name=op.f("ck_product_prices_positive_amount_minor")
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name=op.f("fk_product_prices_sku_id_skus")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_prices")),
        sa.UniqueConstraint("sku_id", "provider", name=op.f("uq_product_prices_sku_id")),
    )
    op.drop_constraint("ck_orders_currency_xtr", "orders", type_="check")
    op.add_column(
        "orders",
        sa.Column(
            "payment_provider",
            sa.String(length=80),
            server_default="telegram_stars",
            nullable=False,
        ),
    )
    op.alter_column("orders", "currency", type_=sa.String(length=12), existing_nullable=False)
    op.alter_column("orders", "payment_provider", server_default=None)
    op.create_table(
        "plugin_payments",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_id", sa.String(length=255), nullable=True),
        sa.Column("provider_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_event", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint(
            "amount_minor >= 1", name=op.f("ck_plugin_payments_positive_amount_minor")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_plugin_payments_order_id_orders")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plugin_payments")),
        sa.UniqueConstraint("order_id", name=op.f("uq_plugin_payments_order_id")),
        sa.UniqueConstraint("provider", "external_id", name=op.f("uq_plugin_payments_provider")),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_plugin_payments_transaction_id")),
    )
    op.create_index(
        "ix_plugin_payments_provider_status",
        "plugin_payments",
        ["provider", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_payments_provider_status", table_name="plugin_payments")
    op.drop_table("plugin_payments")
    op.alter_column("orders", "currency", type_=sa.String(length=3), existing_nullable=False)
    op.create_check_constraint("ck_orders_currency_xtr", "orders", "currency = 'XTR'")
    op.drop_column("orders", "payment_provider")
    op.drop_table("product_prices")
