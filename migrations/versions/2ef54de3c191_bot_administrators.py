"""Add dynamic bot administrators.

Revision ID: 2ef54de3c191
Revises: 7d61a81eb912
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2ef54de3c191"
down_revision: str | Sequence[str] | None = "7d61a81eb912"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_administrators",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64)),
        sa.Column("display_name", sa.String(length=128)),
        sa.Column("added_by_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_administrators")
