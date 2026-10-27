import html
import logging
import uuid
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web

from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.orders import ShopError, create_order
from app.services.plugin_payments import (
    PluginPaymentError,
    fulfill_plugin_payment,
    record_plugin_payment_error,
    reserve_plugin_payment,
    save_plugin_checkout,
    update_plugin_payment_status,
)
from app.services.product_prices import ProductPriceError, set_product_plugin_price
from plugins.bepusdt.config import get_bepusdt_settings
from plugins.bepusdt.gateway import GatewayError, create_transaction
from plugins.bepusdt.signing import verify_signature

logger = logging.getLogger(__name__)
PROVIDER = "bepusdt"


class BepusdtPriceState(StatesGroup):
    amount = State()


class BepusdtPlugin:
    name = PROVIDER

    def __init__(self) -> None:
        self.settings = get_bepusdt_settings()
        self.router = Router(name="plugin_bepusdt")
        self.router.callback_query.register(
            self._confirm_checkout, F.data.startswith("plugin:bepusdt:confirm:")
        )
        self.router.callback_query.register(
            self._checkout, F.data.startswith("plugin:bepusdt:buy:")
        )
        self.router.callback_query.register(
            self._start_price, F.data.startswith("plugin:bepusdt:price:")
        )
        self.router.message.register(self._save_price, BepusdtPriceState.amount)

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        price = product.prices.get(PROVIDER)
        if price is None:
            return None
        return InlineKeyboardButton(
            text=f"₮ USDT / {Decimal(price.amount_minor) / Decimal(100):.2f} {price.currency}",
            callback_data=f"plugin:bepusdt:confirm:{product.id}",
        )

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return InlineKeyboardButton(
            text="设置 BEpusdt 价格",
            callback_data=f"plugin:bepusdt:price:{product_id}",
        )

    def admin_home_buttons(self) -> list[list[InlineKeyboardButton]]:
        return []

    async def startup(self, bot: Bot) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def register_http_routes(self, app: web.Application, bot: Bot) -> None:
        app["bepusdt_bot"] = bot
        app.router.add_post("/plugins/bepusdt/notify", self._notify)

    async def fulfill_paid_order(self, bot: Bot, delivered) -> bool:
        return False

    async def _start_price(self, callback: CallbackQuery, state: FSMContext) -> None:
        from app.core.config import get_settings

        if callback.from_user.id not in get_settings().admin_ids:
            await callback.answer("无权限", show_alert=True)
            return
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await state.set_state(BepusdtPriceState.amount)
        await state.update_data(bepusdt_product_id=str(product_id))
        await callback.answer()
        await callback.message.answer(
            f"请输入 BEpusdt {self.settings.bepusdt_fiat} 价格，例如 9.90；发送 - 可关闭。"
        )

    async def _save_price(self, message: Message, state: FSMContext) -> None:
        from app.core.config import get_settings

        if message.from_user.id not in get_settings().admin_ids:
            return
        raw = (message.text or "").strip()
        try:
            if raw == "-":
                amount_minor = None
            else:
                amount = Decimal(raw)
                if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                    raise ValueError
                amount_minor = int(amount * 100)
            data = await state.get_data()
            product_id = uuid.UUID(data["bepusdt_product_id"])
            async with SessionFactory() as session:
                await set_product_plugin_price(
                    session,
                    product_id=product_id,
                    provider=PROVIDER,
                    currency=self.settings.bepusdt_fiat,
                    amount_minor=amount_minor,
                )
        except (InvalidOperation, ValueError, KeyError, ProductPriceError) as exc:
            await message.answer(f"❌ 无效价格：{html.escape(str(exc))}")
            return
        await state.clear()
        await message.answer("✅ BEpusdt 商品价格已更新。")

    async def _confirm_checkout(self, callback: CallbackQuery) -> None:
        await callback.answer()
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.message.answer("Invalid product.")
            return
        from app.services.orders import get_product

        async with SessionFactory() as session:
            product = await get_product(session, product_id)
        if product is None or "bepusdt" not in product.plugin_prices:
            await callback.message.answer("This payment option is unavailable.")
            return
        currency, amount_minor = product.plugin_prices["bepusdt"]
        await callback.message.answer(
            "🧾 <b>Confirm purchase</b>\n\n"
            f"Product: {html.escape(product.name)}\n"
            f"Price: {Decimal(amount_minor) / Decimal(100):.2f} {currency}\n"
            "Payment: USDT-TRC20\n"
            "Delivery: Instant after payment confirmation",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Continue to payment",
                            callback_data=f"plugin:bepusdt:buy:{product.id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ Back", callback_data=f"shop:product:{product.id}"
                        )
                    ],
                ]
            ),
        )

    async def _checkout(self, callback: CallbackQuery) -> None:
        await callback.answer()
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.message.answer("无效商品。")
            return
        try:
            async with SessionFactory() as session:
                order = await create_order(
                    session,
                    telegram_user_id=callback.from_user.id,
                    username=callback.from_user.username,
                    display_name=callback.from_user.full_name,
                    product_id=product_id,
                    payment_provider=PROVIDER,
                )
            async with SessionFactory() as session:
                payment = await reserve_plugin_payment(
                    session, order_id=order.order_id, provider=PROVIDER
                )
            if payment.status == "waiting" and payment.checkout_url:
                checkout_url = payment.checkout_url
            else:
                try:
                    checkout = await create_transaction(
                        self.settings,
                        merchant_order_id=order.public_id,
                        amount_minor=order.total_amount,
                        product_name=order.product_name,
                    )
                except GatewayError as exc:
                    async with SessionFactory() as session:
                        await record_plugin_payment_error(
                            session,
                            order_id=order.order_id,
                            provider=PROVIDER,
                            error=str(exc),
                        )
                    raise
                async with SessionFactory() as session:
                    await save_plugin_checkout(
                        session,
                        order_id=order.order_id,
                        provider=PROVIDER,
                        external_id=checkout.external_id,
                        checkout_url=checkout.checkout_url,
                        expires_at=checkout.expires_at,
                        provider_data=checkout.data,
                    )
                checkout_url = checkout.checkout_url
        except (ShopError, PluginPaymentError, GatewayError) as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        await callback.message.answer(
            "₮ <b>USDT-TRC20 支付</b>\n\n"
            f"订单：<code>{order.public_id}</code>\n"
            f"金额：{Decimal(order.total_amount) / Decimal(100):.2f} {order.currency}\n\n"
            "请通过下方收银台完成付款。请勿修改收银台显示的 USDT 到账金额。",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="打开 BEpusdt 收银台", url=checkout_url)]
                ]
            ),
        )

    async def _notify(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            return web.Response(status=400, text="invalid json")
        if not isinstance(payload, dict) or not verify_signature(
            payload, self.settings.bepusdt_api_token
        ):
            return web.Response(status=401, text="invalid signature")
        merchant_order_id = payload.get("order_id")
        status = payload.get("status")
        if not isinstance(merchant_order_id, str) or status not in (1, 2, 3, 4, 5, 6):
            return web.Response(status=400, text="invalid callback")
        try:
            async with SessionFactory() as session:
                if status == 2:
                    transaction_id = payload.get("block_transaction_id")
                    if not isinstance(transaction_id, str) or not transaction_id:
                        raise PluginPaymentError("支付成功回调缺少链上交易 ID")
                    token = payload.get("token")
                    if token not in (None, "", "USDT"):
                        raise PluginPaymentError("回调代币不是 USDT")
                    amount_minor = self._amount_minor(payload.get("amount"))
                    delivery = await fulfill_plugin_payment(
                        session,
                        provider=PROVIDER,
                        merchant_order_id=merchant_order_id,
                        transaction_id=transaction_id,
                        amount_minor=amount_minor,
                        event=payload,
                        provider_data={"actual_amount": str(payload.get("actual_amount") or "")},
                    )
                else:
                    delivery = None
                    state = {
                        1: "waiting",
                        3: "expired",
                        4: "canceled",
                        5: "confirming",
                        6: "failed",
                    }[status]
                    await update_plugin_payment_status(
                        session,
                        provider=PROVIDER,
                        merchant_order_id=merchant_order_id,
                        status=state,
                        event=payload,
                    )
        except (PluginPaymentError, InvalidOperation, ValueError) as exc:
            logger.warning("invalid BEpusdt callback: %s", exc)
            return web.Response(status=400, text="invalid callback")
        except Exception:
            logger.exception("BEpusdt callback persistence failed")
            return web.Response(status=500, text="retry")
        if delivery:
            if delivery.needs_supplier:
                from app.plugins.registry import get_plugin_registry

                handled = False
                for plugin in get_plugin_registry().plugins:
                    if await plugin.fulfill_paid_order(request.app["bepusdt_bot"], delivery):
                        handled = True
                        break
                if not handled:
                    await request.app["bepusdt_bot"].send_message(
                        delivery.telegram_user_id,
                        "⚠️ 支付成功，但上游供货插件不可用，请联系客服。",
                    )
            else:
                await self._notify_user(request.app["bepusdt_bot"], delivery)
        return web.Response(text="ok")

    @staticmethod
    def _amount_minor(value) -> int:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise ValueError("invalid fiat amount")
        return int(amount * 100)

    @staticmethod
    async def _notify_user(bot: Bot, delivery) -> None:
        try:
            if delivery.should_deliver:
                await bot.send_message(
                    delivery.telegram_user_id,
                    "✅ <b>USDT 支付成功，商品已交付</b>\n\n"
                    f"商品：{html.escape(delivery.product_name)}\n"
                    f"订单：<code>{delivery.public_id}</code>\n"
                    f"卡密：<code>{html.escape(delivery.secret)}</code>",
                )
            elif delivery.exception_message:
                await bot.send_message(
                    delivery.telegram_user_id,
                    f"⚠️ 订单 <code>{delivery.public_id}</code>："
                    f"{html.escape(delivery.exception_message)}",
                )
        except Exception:
            logger.exception("failed to notify BEpusdt payer for %s", delivery.public_id)


def create_plugin() -> BepusdtPlugin:
    return BepusdtPlugin()
