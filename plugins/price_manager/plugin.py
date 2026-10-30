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
from app.services.product_prices import ProductPriceError, set_product_plugin_price


class EditPrice(StatesGroup):
    amount = State()


class PriceManagerPlugin:
    name = "price_manager"
    supplier_provider = False

    def __init__(self) -> None:
        self.router = Router(name="plugin_price_manager")
        self.router.callback_query.register(self._list, F.data == "plugin:prices:list")
        self.router.callback_query.register(
            self._product, F.data.startswith("plugin:prices:product:")
        )
        self.router.callback_query.register(
            self._start_edit, F.data.startswith("plugin:prices:edit:")
        )
        self.router.callback_query.register(
            self._disable, F.data.startswith("plugin:prices:disable:")
        )
        self.router.message.register(self._finish_edit, EditPrice.amount)

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return InlineKeyboardButton(
            text="💰 价格设置", callback_data=f"plugin:prices:product:{product_id}"
        )

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
                    text=product.name[:30],
                    callback_data=f"plugin:prices:product:{product.id}",
                )
            ]
            for product in products
        ]
        rows.append([InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")])
        await callback.message.answer(
            "💰 <b>价格设置</b>\n请选择商品：",
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
        pricing_mode = (usdt_price.metadata_json or {}).get("pricing") if usdt_price else None
        pricing_label = {
            "auto_markup": "自动跟价",
            "manual_protected": "手动定价（利润保护）",
        }.get(pricing_mode, "普通手动价格")
        lines = [
            f"💰 <b>{html.escape(product.name)}</b>",
            f"Stars：⭐ {sku.price_xtr} · {'启用' if sku.stars_enabled else '停用'}",
        ]
        lines.append(f"USDT 定价策略：{pricing_label}")
        for provider, label in (("usdt", "USDT"), ("balance", "余额")):
            price = by_provider.get(provider)
            lines.append(
                f"{label}："
                + (
                    f"${price.amount_minor / 100:.2f} {price.currency} · "
                    f"{'启用' if price.is_active else '停用'}"
                    if price
                    else "未设置"
                )
            )
        rows = [
            [
                InlineKeyboardButton(
                    text="⭐ 设置 Stars", callback_data=f"plugin:prices:edit:{product_id}:stars"
                )
            ],
            [
                InlineKeyboardButton(
                    text="₮ 设置 USDT", callback_data=f"plugin:prices:edit:{product_id}:usdt"
                ),
                InlineKeyboardButton(
                    text="💵 设置余额", callback_data=f"plugin:prices:edit:{product_id}:balance"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="停用 USDT", callback_data=f"plugin:prices:disable:{product_id}:usdt"
                ),
                InlineKeyboardButton(
                    text="停用余额", callback_data=f"plugin:prices:disable:{product_id}:balance"
                ),
            ],
            [InlineKeyboardButton(text="« 价格设置", callback_data="plugin:prices:list")],
        ]
        await callback.answer()
        await callback.message.answer(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def _start_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            _, _, _, raw_product_id, provider = callback.data.split(":", 4)
            product_id = uuid.UUID(raw_product_id)
            if provider not in {"stars", "usdt", "balance"}:
                raise ValueError
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await state.update_data(price_product_id=str(product_id), price_provider=provider)
        await state.set_state(EditPrice.amount)
        await callback.answer()
        prompt = "请输入 Stars 正整数价格" if provider == "stars" else "请输入美元价格，例如 4.99"
        await callback.message.answer(f"{prompt}；发送 /cancel 取消。")

    async def _finish_edit(self, message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        data = await state.get_data()
        try:
            product_id = uuid.UUID(data["price_product_id"])
            provider = data["price_provider"]
            if provider == "stars":
                amount = int((message.text or "").strip())
                if amount < 1:
                    raise ValueError
                async with SessionFactory() as session, session.begin():
                    sku = await session.scalar(
                        select(SKU)
                        .where(SKU.product_id == product_id, SKU.is_default.is_(True))
                        .with_for_update()
                    )
                    if sku is None:
                        raise ProductPriceError("商品不存在")
                    sku.price_xtr = amount
                    sku.stars_enabled = True
                    await record_admin_action(
                        session,
                        admin_telegram_id=message.from_user.id,
                        action="product_price_set",
                        target_type="product",
                        target_id=str(product_id),
                        details={"provider": "stars", "amount": amount},
                    )
            else:
                decimal_amount = Decimal((message.text or "").strip())
                if not decimal_amount.is_finite() or decimal_amount <= 0:
                    raise ValueError
                amount = int((decimal_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                async with SessionFactory() as session:
                    await set_product_plugin_price(
                        session,
                        product_id=product_id,
                        provider=provider,
                        currency="USD",
                        amount_minor=amount,
                    )
                if provider == "usdt":
                    async with SessionFactory() as session, session.begin():
                        sku = await session.scalar(
                            select(SKU).where(
                                SKU.product_id == product_id, SKU.is_default.is_(True)
                            )
                        )
                        price = await session.scalar(
                            select(ProductPrice).where(
                                ProductPrice.sku_id == sku.id,
                                ProductPrice.provider == "usdt",
                            )
                        )
                        if price is not None:
                            price.metadata_json = {
                                **(price.metadata_json or {}),
                                "pricing": "manual_protected",
                            }
                async with SessionFactory() as session, session.begin():
                    await record_admin_action(
                        session,
                        admin_telegram_id=message.from_user.id,
                        action="product_price_set",
                        target_type="product",
                        target_id=str(product_id),
                        details={"provider": provider, "amount_minor": amount},
                    )
        except (KeyError, ValueError, InvalidOperation, ProductPriceError):
            await message.answer("价格格式无效，请重新输入。")
            return
        await state.clear()
        await message.answer(
            "✅ 价格已更新。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="返回商品价格",
                            callback_data=f"plugin:prices:product:{product_id}",
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
            _, _, _, raw_product_id, provider = callback.data.split(":", 4)
            product_id = uuid.UUID(raw_product_id)
            if provider not in {"usdt", "balance"}:
                raise ValueError
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            await set_product_plugin_price(
                session,
                product_id=product_id,
                provider=provider,
                currency="USD",
                amount_minor=None,
            )
        async with SessionFactory() as session, session.begin():
            await record_admin_action(
                session,
                admin_telegram_id=callback.from_user.id,
                action="product_price_disable",
                target_type="product",
                target_id=str(product_id),
                details={"provider": provider},
            )
        await callback.answer("已停用", show_alert=True)
        await callback.message.answer(
            "✅ 价格已停用。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="返回商品价格",
                            callback_data=f"plugin:prices:product:{product_id}",
                        )
                    ]
                ]
            ),
        )


def create_plugin() -> PriceManagerPlugin:
    return PriceManagerPlugin()
