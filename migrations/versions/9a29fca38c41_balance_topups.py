"""Add balance top-up review records.

Revision ID: 9a29fca38c41
Revises: b2371e927d73
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a29fca38c41"
down_revision: str | Sequence[str] | None = "b2371e927d73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("_tgfakabot_schema_bootstrap"):
        return
    op.create_table(
        "balance_topups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("proof", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_telegram_id", sa.BigInteger()),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "amount_minor >= 10", name=op.f("ck_balance_topups_minimum_topup_amount")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_balance_topups_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_balance_topups")),
        sa.UniqueConstraint("method", "proof", name=op.f("uq_balance_topups_method")),
    )
    op.create_index("ix_balance_topups_status_created", "balance_topups", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_balance_topups_status_created", table_name="balance_topups")
    op.drop_table("balance_topups")
