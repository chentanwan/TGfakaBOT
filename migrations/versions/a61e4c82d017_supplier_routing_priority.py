"""Add supplier routing priority.

Revision ID: a61e4c82d017
Revises: e3b71a90fd25
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a61e4c82d017"
down_revision: str | Sequence[str] | None = "e3b71a90fd25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.add_column(
        "supplier_products",
        sa.Column("routing_priority", sa.Integer(), server_default="100", nullable=False),
    )
    op.alter_column("supplier_products", "routing_priority", server_default=None)


def downgrade() -> None:
    op.drop_column("supplier_products", "routing_priority")
