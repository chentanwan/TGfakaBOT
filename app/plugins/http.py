import logging

from aiogram import Bot
from aiohttp import web

from app.core.config import get_settings
from app.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


async def start_plugin_http_server(bot: Bot, registry: PluginRegistry) -> web.AppRunner | None:
    if not registry.plugins:
        return None
    settings = get_settings()
    app = web.Application(client_max_size=128 * 1024)
    app.router.add_get("/plugins/health", lambda _: web.Response(text="ok"))
    for plugin in registry.plugins:
        plugin.register_http_routes(app, bot)
    runner = web.AppRunner(app, access_log=logger)
    await runner.setup()
    await web.TCPSite(runner, settings.plugin_http_host, settings.plugin_http_port).start()
    logger.info(
        "plugin HTTP server listening on %s:%s",
        settings.plugin_http_host,
        settings.plugin_http_port,
    )
    return runner
