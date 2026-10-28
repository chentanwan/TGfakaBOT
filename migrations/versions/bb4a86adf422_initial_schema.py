"""Initial schema.

Revision ID: bb4a86adf422
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

from app.db import Base

revision: str = "bb4a86adf422"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        "CREATE TABLE IF NOT EXISTS _tgfakabot_schema_bootstrap "
        "(id integer PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now())"
    )
    op.execute("INSERT INTO _tgfakabot_schema_bootstrap (id) VALUES (1) ON CONFLICT DO NOTHING")


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    op.execute("DROP TABLE IF EXISTS _tgfakabot_schema_bootstrap")
