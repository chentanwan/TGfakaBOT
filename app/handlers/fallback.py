from aiogram import Router
from aiogram.types import CallbackQuery

router = Router(name="callback-fallback")


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    """Acknowledge buttons from stale messages instead of leaving Telegram spinning."""
    await callback.answer("该菜单已更新，请重新打开最新菜单。", show_alert=True)
