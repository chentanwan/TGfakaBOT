"""Add formatted product descriptions.

Revision ID: d697b6cedbbd
Revises: c01ab8ca51d7
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d697b6cedbbd"
down_revision: str | Sequence[str] | None = "c01ab8ca51d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("description_html", sa.Text(), server_default="", nullable=False),
    )
    # Existing plain descriptions use the escaped fallback until edited again.
    op.alter_column("products", "description_html", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "description_html")
