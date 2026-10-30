from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web

from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.admin_access import (
    add_dynamic_admin,
    all_admin_ids,
    is_admin,
    is_root_admin,
    list_dynamic_admins,
    remove_dynamic_admin,
)
from app.services.audit import record_admin_action

logger = logging.getLogger(__name__)


class AddAdministrator(StatesGroup):
    telegram_id = State()


class AdminManagerPlugin:
    name = "admin_manager"
    supplier_provider = False

    def __init__(self) -> None:
        self.router = Router(name="plugin_admin_manager")
        self.router.callback_query.register(self._list, F.data == "plugin:adminmgr:list")
        self.router.callback_query.register(self._start_add, F.data == "plugin:adminmgr:add")
        self.router.callback_query.register(
            self._confirm_remove, F.data.startswith("plugin:adminmgr:remove:")
        )
        self.router.callback_query.register(
            self._remove, F.data.startswith("plugin:adminmgr:confirmremove:")
        )
        self.router.message.register(self._finish_add, AddAdministrator.telegram_id)

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id) -> InlineKeyboardButton | None:
        return None

    def admin_home_buttons(self) -> list[list[InlineKeyboardButton]]:
        return []

    async def startup(self, bot: Bot) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def register_http_routes(self, app: web.Application, bot: Bot) -> None:
        return None

    async def fulfill_paid_order(self, bot: Bot, delivered: object) -> bool:
        return False

    @staticmethod
    def _back() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")]]
        )

    async def _list(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer()
        async with SessionFactory() as session:
            dynamic = await list_dynamic_admins(session)
        lines = ["🛡 <b>管理员管理</b>", "", "<b>初始超级管理员</b>"]
        root_ids = sorted(user_id for user_id in all_admin_ids() if is_root_admin(user_id))
        lines.extend(f"• <code>{user_id}</code> · 不可删除" for user_id in root_ids)
        lines.append("\n<b>Telegram 动态管理员</b>")
        rows: list[list[InlineKeyboardButton]] = []
        if not dynamic:
            lines.append("• 暂无")
        for administrator in dynamic:
            name = administrator.display_name or administrator.username or "—"
            lines.append(f"• <code>{administrator.telegram_user_id}</code> · {html.escape(name)}")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 删除 {administrator.telegram_user_id}",
                        callback_data=(f"plugin:adminmgr:remove:{administrator.telegram_user_id}"),
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="➕ 新增管理员", callback_data="plugin:adminmgr:add")]
        )
        rows.append([InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")])
        await callback.message.answer(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def _start_add(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer()
        await state.set_state(AddAdministrator.telegram_id)
        await callback.message.answer(
            "请输入要添加的 Telegram 数字 ID。\n"
            "目标用户必须先与机器人对话，才能接收状态通知。\n"
            "发送 /cancel 取消。"
        )

    async def _finish_add(self, message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        try:
            telegram_id = int((message.text or "").strip())
            if telegram_id <= 0:
                raise ValueError
        except ValueError:
            await message.answer("请输入有效的 Telegram 正整数 ID。")
            return
        if is_admin(telegram_id):
            await message.answer("该用户已经是管理员。", reply_markup=self._back())
            await state.clear()
            return
        try:
            chat = await message.bot.get_chat(telegram_id)
        except Exception:
            await message.answer("无法访问该用户。请让对方先向机器人发送 /start 后重试。")
            return
        display_name = " ".join(part for part in [chat.first_name, chat.last_name] if part)
        async with SessionFactory() as session, session.begin():
            _, created = await add_dynamic_admin(
                session,
                telegram_user_id=telegram_id,
                added_by_telegram_id=message.from_user.id,
                username=chat.username,
                display_name=display_name or None,
            )
            if created:
                await record_admin_action(
                    session,
                    admin_telegram_id=message.from_user.id,
                    action="administrator_add",
                    target_type="telegram_user",
                    target_id=str(telegram_id),
                )
        await state.clear()
        if not created:
            await message.answer("该用户已经是管理员。", reply_markup=self._back())
            return
        await message.answer(
            f"✅ 已添加管理员 <code>{telegram_id}</code>。", reply_markup=self._back()
        )
        try:
            await message.bot.send_message(
                telegram_id,
                "🛡 您已被添加为机器人管理员。发送 /admin 打开管理后台。",
            )
        except Exception:
            logger.warning("failed to notify new administrator %s", telegram_id, exc_info=True)

    async def _confirm_remove(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            telegram_id = int(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        if is_root_admin(telegram_id):
            await callback.answer("初始超级管理员不可删除", show_alert=True)
            return
        if telegram_id == callback.from_user.id:
            await callback.answer("不能删除自己的管理员权限", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            f"确认删除管理员 <code>{telegram_id}</code>？",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="确认删除",
                            callback_data=f"plugin:adminmgr:confirmremove:{telegram_id}",
                        )
                    ],
                    [InlineKeyboardButton(text="取消", callback_data="plugin:adminmgr:list")],
                ]
            ),
        )

    async def _remove(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            telegram_id = int(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        if is_root_admin(telegram_id) or telegram_id == callback.from_user.id:
            await callback.answer("不能执行该删除操作", show_alert=True)
            return
        async with SessionFactory() as session, session.begin():
            removed = await remove_dynamic_admin(session, telegram_id)
            if removed:
                await record_admin_action(
                    session,
                    admin_telegram_id=callback.from_user.id,
                    action="administrator_remove",
                    target_type="telegram_user",
                    target_id=str(telegram_id),
                )
        await callback.answer("已删除" if removed else "管理员不存在", show_alert=True)
        await callback.message.answer(
            f"✅ 已删除管理员 <code>{telegram_id}</code>。" if removed else "管理员不存在。",
            reply_markup=self._back(),
        )
        if removed:
            try:
                await callback.bot.send_message(telegram_id, "您的机器人管理员权限已被移除。")
            except Exception:
                logger.warning(
                    "failed to notify removed administrator %s", telegram_id, exc_info=True
                )


def create_plugin() -> AdminManagerPlugin:
    return AdminManagerPlugin()
