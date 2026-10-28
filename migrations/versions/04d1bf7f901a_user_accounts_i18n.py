"""Add user account, language, points and referral fields.

Revision ID: 04d1bf7f901a
Revises: f6230ee498d2
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "04d1bf7f901a"
down_revision: str | Sequence[str] | None = "f6230ee498d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.add_column(
        "users", sa.Column("language_code", sa.String(10), server_default="en", nullable=False)
    )
    op.add_column(
        "users", sa.Column("balance_minor", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column("users", sa.Column("points", sa.BigInteger(), server_default="0", nullable=False))
    op.add_column(
        "users", sa.Column("tier", sa.String(32), server_default="bronze", nullable=False)
    )
    op.add_column(
        "users",
        sa.Column("total_spent_minor", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "users", sa.Column("order_count", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column("users", sa.Column("referral_code", sa.String(80), nullable=True))
    op.add_column("users", sa.Column("referred_by_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "users",
        sa.Column("referral_reward_minor", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_unique_constraint("uq_users_referral_code", "users", ["referral_code"])
    op.create_foreign_key(
        "fk_users_referred_by_user_id_users",
        "users",
        "users",
        ["referred_by_user_id"],
        ["id"],
    )
    op.execute("UPDATE users SET referral_code = 'REF_' || telegram_user_id")
    op.alter_column("users", "referral_code", nullable=False)


def downgrade() -> None:
    op.drop_constraint("fk_users_referred_by_user_id_users", "users", type_="foreignkey")
    op.drop_constraint("uq_users_referral_code", "users", type_="unique")
    for column in (
        "referral_reward_minor",
        "referred_by_user_id",
        "referral_code",
        "order_count",
        "total_spent_minor",
        "tier",
        "points",
        "balance_minor",
        "language_code",
    ):
        op.drop_column("users", column)
