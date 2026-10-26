import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.core.config import get_settings
from app.handlers import admin, catalog, charge_admin, payment_admin
from app.plugins import configure_plugins
from app.plugins.http import start_plugin_http_server
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
    plugin_registry = configure_plugins(settings.plugin_modules)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(charge_admin.router)
    dispatcher.include_router(payment_admin.router)
    dispatcher.include_router(catalog.router)
    for plugin in plugin_registry.plugins:
        if plugin.router is not None:
            dispatcher.include_router(plugin.router)
    # Never drop queued updates: a pending successful_payment is a financial event.
    await bot.delete_webhook(drop_pending_updates=False)
    plugin_http_runner = await start_plugin_http_server(bot, plugin_registry)
    for plugin in plugin_registry.plugins:
        await plugin.startup(bot)
    cleanup_task = asyncio.create_task(cleanup_loop(bot))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        for plugin in reversed(plugin_registry.plugins):
            await plugin.shutdown()
        if plugin_http_runner is not None:
            await plugin_http_runner.cleanup()
        await redis.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
