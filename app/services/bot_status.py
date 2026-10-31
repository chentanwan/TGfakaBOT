from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from html import escape

from aiogram import Bot
from redis.asyncio import Redis

from app.services.admin_access import all_admin_ids

logger = logging.getLogger(__name__)
HEARTBEAT_KEY = "tgfakabot:status:heartbeat"
OFFLINE_ALERT_KEY = "tgfakabot:status:offline-alerted"
INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
_STATUS = {
    "online": ("🟢", "运行正常", "服务已启动，可以正常接收消息。"),
    "recovered": ("🟢", "服务已恢复", "心跳恢复，服务已重新上线。"),
    "offline": ("🔴", "服务异常", "心跳中断，请检查容器、网络或运行日志。"),
    "stopping": ("🟡", "服务重启", "服务正在正常停止或重启。"),
}


def _status_text(status: str, detail: str | None = None) -> str:
    icon, title, default_detail = _STATUS.get(status, ("ℹ️", "状态更新", "服务状态已更新。"))
    now = datetime.now(UTC).astimezone().strftime("%m-%d %H:%M:%S")
    message = escape(detail or default_detail)
    return (
        f"{icon} <b>{title}</b>\n"
        f"{message}\n\n"
        f"🕒 <code>{now}</code>  ·  🖥 <code>{escape(INSTANCE_ID)}</code>"
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
    previous = await redis.get(OFFLINE_ALERT_KEY)
    await redis.delete(OFFLINE_ALERT_KEY)
    status = "recovered" if previous == b"unexpected" else "online"
    await notify_administrators(bot, _status_text(status))


async def notify_stopping(bot: Bot, redis: Redis) -> None:
    await notify_administrators(bot, _status_text("stopping"))
    await redis.delete(HEARTBEAT_KEY)
    await redis.set(OFFLINE_ALERT_KEY, "graceful", ex=600)


async def heartbeat_loop(redis: Redis, interval_seconds: int = 10) -> None:
    while True:
        await redis.set(HEARTBEAT_KEY, INSTANCE_ID, ex=30)
        await asyncio.sleep(interval_seconds)
