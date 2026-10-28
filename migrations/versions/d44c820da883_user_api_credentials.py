"""Add user API credentials.

Revision ID: d44c820da883
Revises: 9a29fca38c41
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d44c820da883"
down_revision: str | Sequence[str] | None = "9a29fca38c41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    op.create_table(
        "user_api_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("request_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_api_credentials_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_api_credentials")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_user_api_credentials_key_hash")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_api_credentials_user_id")),
    )


def downgrade() -> None:
    op.drop_table("user_api_credentials")
