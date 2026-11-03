from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def replace_callback_message(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Replace the message that owns a menu button, with a safe fallback."""
    try:
        return await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return callback.message
        logger.debug("menu message cannot be edited; replacing it", exc_info=True)
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        logger.debug("old menu message cannot be deleted", exc_info=True)
    return await callback.message.answer(text, reply_markup=reply_markup)
