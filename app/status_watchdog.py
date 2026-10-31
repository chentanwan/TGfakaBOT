from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from redis.asyncio import Redis

from app.core.config import get_settings
from app.services.admin_access import refresh_admin_cache
from app.services.bot_status import (
    HEARTBEAT_KEY,
    OFFLINE_ALERT_KEY,
    _status_text,
    notify_administrators,
)

logger = logging.getLogger(__name__)


async def watchdog_loop() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    redis = Redis.from_url(settings.redis_url)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    was_online = False
    try:
        await refresh_admin_cache()
        while True:
            online = bool(await redis.exists(HEARTBEAT_KEY))
            if online:
                was_online = True
                await redis.delete(OFFLINE_ALERT_KEY)
            elif was_online:
                claimed = await redis.set(OFFLINE_ALERT_KEY, "unexpected", ex=3600, nx=True)
                if claimed:
                    await refresh_admin_cache()
                    await notify_administrators(
                        bot,
                        _status_text("offline"),
                    )
                was_online = False
            await asyncio.sleep(10)
    finally:
        await redis.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(watchdog_loop())
