from __future__ import annotations

import html
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web
from sqlalchemy import select

from app.db.models import SKU, Product, ProductPrice
from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.admin_access import is_admin
from app.services.audit import record_admin_action
from app.services.product_prices import ProductPriceError, set_product_usd_price


class EditPrice(StatesGroup):
    amount = State()


class PriceManagerPlugin:
    name = "price_manager"
    supplier_provider = False

    def __init__(self) -> None:
        self.router = Router(name="plugin_price_manager")
        self.router.callback_query.register(self._list, F.data == "plugin:prices:list")
        self.router.callback_query.register(self._product, F.data.startswith("pp:p:"))
        self.router.callback_query.register(self._start_edit, F.data.startswith("pp:e:"))
        self.router.callback_query.register(self._disable, F.data.startswith("pp:d:"))
        self.router.message.register(self._finish_edit, EditPrice.amount)

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return InlineKeyboardButton(text="💰 定价", callback_data=f"pp:p:{product_id}")

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

    async def _list(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer()
        async with SessionFactory() as session:
            products = list(
                await session.scalars(select(Product).order_by(Product.created_at.desc()))
            )
        rows = [
            [
                InlineKeyboardButton(
                    text=f"💰 {product.name[:28]}",
                    callback_data=f"pp:p:{product.id}",
                )
            ]
            for product in products
        ]
        rows.append([InlineKeyboardButton(text="🏠 管理后台", callback_data="adm:home")])
        await callback.message.answer(
            "💰 <b>商品定价</b>\n请选择商品：",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _product(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            row = (
                await session.execute(
                    select(Product, SKU)
                    .join(SKU, SKU.product_id == Product.id)
                    .where(Product.id == product_id, SKU.is_default.is_(True))
                )
            ).one_or_none()
            if row is None:
                await callback.answer("商品不存在", show_alert=True)
                return
            product, sku = row
            prices = list(
                await session.scalars(select(ProductPrice).where(ProductPrice.sku_id == sku.id))
            )
        by_provider = {price.provider: price for price in prices}
        usdt_price = by_provider.get("usdt")
        balance_price = by_provider.get("balance")
        current = usdt_price or balance_price
        enabled = bool(
            usdt_price and balance_price and usdt_price.is_active and balance_price.is_active
        )
        pricing_mode = (current.metadata_json or {}).get("pricing") if current else None
        pricing_label = {
            "auto_fixed": "自动定价 · 固定利润",
            "auto_percent": "自动定价 · 比例利润",
            "auto_markup": "自动定价 · 组合利润",
            "manual_protected": "手动定价",
        }.get(pricing_mode, "手动定价")
        price_text = f"${current.amount_minor / 100:.2f}" if current else "未设置"
        status = "✅ USDT 与余额均已启用" if enabled else "⏸ USDT 与余额已停用"
        rows = [
            [
                InlineKeyboardButton(text="✏️ 设置美元售价", callback_data=f"pp:e:{product_id}"),
                InlineKeyboardButton(text="⏸ 停用全部支付", callback_data=f"pp:d:{product_id}"),
            ],
            [InlineKeyboardButton(text="⬅️ 返回价格设置", callback_data="plugin:prices:list")],
        ]
        await callback.answer()
        await callback.message.answer(
            "\n".join(
                [
                    f"💰 <b>{html.escape(product.name)}</b>",
                    f"💵 美元售价：{price_text}",
                    f"📊 定价模式：{pricing_label}",
                    f"💳 支付状态：{status}",
                ]
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _start_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await state.update_data(price_product_id=str(product_id))
        await state.set_state(EditPrice.amount)
        await callback.answer()
        await callback.message.answer(
            "✏️ 请输入美元售价，例如 4.99。\n保存后 USDT 与账户余额将同时启用；发送 /cancel 取消。"
        )

    async def _finish_edit(self, message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        try:
            product_id = uuid.UUID(data["price_product_id"])
            decimal_amount = Decimal((message.text or "").strip())
            if not decimal_amount.is_finite() or decimal_amount <= 0:
                raise ValueError
            amount = int((decimal_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            metadata = {"pricing": "manual_protected"}
            async with SessionFactory() as session:
                await set_product_usd_price(
                    session,
                    product_id=product_id,
                    amount_minor=amount,
                    metadata_json=metadata,
                )
            async with SessionFactory() as session, session.begin():
                await record_admin_action(
                    session,
                    admin_telegram_id=message.from_user.id,
                    action="product_price_set",
                    target_type="product",
                    target_id=str(product_id),
                    details={"providers": ["usdt", "balance"], "amount_minor": amount},
                )
        except (KeyError, ValueError, InvalidOperation, ProductPriceError):
            await message.answer("❌ 价格格式无效，请重新输入。")
            return
        await state.clear()
        await message.answer(
            "✅ 美元售价已更新，USDT 与余额支付均已启用。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ 返回商品定价", callback_data=f"pp:p:{product_id}"
                        )
                    ]
                ]
            ),
        )

    async def _disable(self, callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            await set_product_usd_price(session, product_id=product_id, amount_minor=None)
        async with SessionFactory() as session, session.begin():
            await record_admin_action(
                session,
                admin_telegram_id=callback.from_user.id,
                action="product_price_disable",
                target_type="product",
                target_id=str(product_id),
                details={"providers": ["usdt", "balance"]},
            )
        await callback.answer("已停用", show_alert=True)
        await callback.message.answer(
            "⏸ USDT 与余额支付均已停用。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ 返回商品定价", callback_data=f"pp:p:{product_id}"
                        )
                    ]
                ]
            ),
        )


def create_plugin() -> PriceManagerPlugin:
    return PriceManagerPlugin()
