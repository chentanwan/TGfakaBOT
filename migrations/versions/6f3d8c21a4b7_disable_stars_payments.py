"""Disable Telegram Stars for all SKUs.

Revision ID: 6f3d8c21a4b7
Revises: 2ef54de3c191
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f3d8c21a4b7"
down_revision: str | Sequence[str] | None = "2ef54de3c191"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    marker = bind.execute(
        sa.text("SELECT to_regclass('_tgfakabot_schema_bootstrap')")
    ).scalar_one_or_none()
    if marker is not None:
        return
    bind.execute(sa.text("UPDATE skus SET stars_enabled = false WHERE stars_enabled = true"))


def downgrade() -> None:
    # Stars cannot be safely re-enabled globally because some SKUs were intentionally disabled.
    pass
