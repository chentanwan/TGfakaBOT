from __future__ import annotations

import asyncio

from sqlalchemy import delete

from app.core.config import get_settings
from app.db.models import BotAdministrator
from app.db.session import SessionFactory
from app.services.admin_access import (
    add_dynamic_admin,
    is_admin,
    is_root_admin,
    refresh_admin_cache,
    remove_dynamic_admin,
)

TEST_ID = 9876543210123


async def main() -> None:
    async with SessionFactory() as session, session.begin():
        await session.execute(
            delete(BotAdministrator).where(BotAdministrator.telegram_user_id == TEST_ID)
        )
    await refresh_admin_cache()
    assert not is_admin(TEST_ID)
    async with SessionFactory() as session, session.begin():
        _, created = await add_dynamic_admin(
            session,
            telegram_user_id=TEST_ID,
            added_by_telegram_id=next(iter(get_settings().admin_ids)),
            username="isolated_test",
        )
        assert created
    assert is_admin(TEST_ID)
    assert not is_root_admin(TEST_ID)
    async with SessionFactory() as session, session.begin():
        assert await remove_dynamic_admin(session, TEST_ID)
    assert not is_admin(TEST_ID)
    print("isolated_admin_manager_ok")


if __name__ == "__main__":
    asyncio.run(main())
