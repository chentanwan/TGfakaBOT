import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.core.config import get_settings
from app.handlers import admin, catalog, charge_admin, payment_admin
from app.services.cleanup import cleanup_loop


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    redis = Redis.from_url(settings.redis_url)
    storage = RedisStorage(redis=redis)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(charge_admin.router)
    dispatcher.include_router(payment_admin.router)
    dispatcher.include_router(catalog.router)
    # Never drop queued updates: a pending successful_payment is a financial event.
    await bot.delete_webhook(drop_pending_updates=False)
    cleanup_task = asyncio.create_task(cleanup_loop(bot))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await redis.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
