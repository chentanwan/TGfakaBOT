from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime

from aiogram import Bot
from redis.asyncio import Redis

from app.services.admin_access import all_admin_ids

logger = logging.getLogger(__name__)
HEARTBEAT_KEY = "tgfakabot:status:heartbeat"
OFFLINE_ALERT_KEY = "tgfakabot:status:offline-alerted"
INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _status_text(status: str, detail: str) -> str:
    icon = {"online": "🟢", "offline": "🔴", "stopping": "🟠"}.get(status, "ℹ️")
    now = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return (
        f"{icon} <b>Bot 状态通知</b>\n\n"
        f"状态：{status}\n"
        f"时间：<code>{now}</code>\n"
        f"实例：<code>{INSTANCE_ID}</code>\n"
        f"说明：{detail}"
    )


async def notify_administrators(bot: Bot, text: str) -> None:
    for administrator_id in sorted(all_admin_ids()):
        try:
            await bot.send_message(administrator_id, text)
        except Exception:
            logger.warning(
                "failed to send bot status to administrator %s",
                administrator_id,
                exc_info=True,
            )


async def notify_online(bot: Bot, redis: Redis) -> None:
    await redis.delete(OFFLINE_ALERT_KEY)
    await notify_administrators(bot, _status_text("online", "Bot 已启动并开始接收消息。"))


async def notify_stopping(bot: Bot, redis: Redis) -> None:
    await notify_administrators(bot, _status_text("stopping", "Bot 正常停止或正在重启。"))
    await redis.delete(HEARTBEAT_KEY)
    await redis.set(OFFLINE_ALERT_KEY, "graceful", ex=600)


async def heartbeat_loop(redis: Redis, interval_seconds: int = 10) -> None:
    while True:
        await redis.set(HEARTBEAT_KEY, INSTANCE_ID, ex=30)
        await asyncio.sleep(interval_seconds)
