from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import BotAdministrator
from app.db.session import SessionFactory

_dynamic_admin_ids: frozenset[int] = frozenset()


def root_admin_ids() -> frozenset[int]:
    return frozenset(get_settings().admin_ids)


def all_admin_ids() -> frozenset[int]:
    return root_admin_ids() | _dynamic_admin_ids


def is_admin(telegram_user_id: int) -> bool:
    return telegram_user_id in all_admin_ids()


def is_root_admin(telegram_user_id: int) -> bool:
    return telegram_user_id in root_admin_ids()


async def refresh_admin_cache() -> frozenset[int]:
    global _dynamic_admin_ids
    async with SessionFactory() as session:
        ids = await session.scalars(select(BotAdministrator.telegram_user_id))
        _dynamic_admin_ids = frozenset(ids)
    return all_admin_ids()


async def list_dynamic_admins(session: AsyncSession) -> list[BotAdministrator]:
    result = await session.scalars(
        select(BotAdministrator).order_by(
            BotAdministrator.created_at, BotAdministrator.telegram_user_id
        )
    )
    return list(result)


async def add_dynamic_admin(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    added_by_telegram_id: int,
    username: str | None = None,
    display_name: str | None = None,
) -> tuple[BotAdministrator, bool]:
    global _dynamic_admin_ids
    existing = await session.scalar(
        select(BotAdministrator).where(BotAdministrator.telegram_user_id == telegram_user_id)
    )
    if existing is not None:
        return existing, False
    administrator = BotAdministrator(
        telegram_user_id=telegram_user_id,
        username=username[:64] if username else None,
        display_name=display_name[:128] if display_name else None,
        added_by_telegram_id=added_by_telegram_id,
    )
    session.add(administrator)
    await session.flush()
    _dynamic_admin_ids = _dynamic_admin_ids | {telegram_user_id}
    return administrator, True


async def remove_dynamic_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    global _dynamic_admin_ids
    administrator = await session.scalar(
        select(BotAdministrator).where(BotAdministrator.telegram_user_id == telegram_user_id)
    )
    if administrator is None:
        return False
    await session.delete(administrator)
    await session.flush()
    _dynamic_admin_ids = _dynamic_admin_ids - {telegram_user_id}
    return True
