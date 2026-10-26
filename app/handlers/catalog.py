import html
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.plugins.base import PluginPrice, PluginProduct
from app.plugins.registry import get_plugin_registry
from app.services.orders import (
    ShopError,
    cancel_unpaid_order,
    create_order,
    get_user_order,
    list_products,
    list_user_orders,
    match_and_fulfill_payment,
    submit_payment_receipt,
    validate_pre_checkout,
)

router = Router(name="catalog")
settings = get_settings()
ORDERS_PAGE_SIZE = 5


def _shop_keyboard(products: list) -> InlineKeyboardMarkup:
    rows = []
    registry = get_plugin_registry()
    for product in products:
        if product.stock < 1 and not product.supplier_stock:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{product.name} · ⭐ {product.price_xtr} · 库存 {product.stock}",
                    callback_data=f"buy:{product.id}",
                )
            ]
        )
        plugin_product = PluginProduct(
            id=product.id,
            name=product.name,
            prices={
                provider: PluginPrice(provider, currency, amount)
                for provider, (currency, amount) in product.plugin_prices.items()
            },
        )
        plugin_buttons = [
            button
            for plugin in registry.plugins
            if (button := plugin.catalog_button(plugin_product)) is not None
        ]
        if plugin_buttons:
            rows.append(plugin_buttons)
    rows.append([InlineKeyboardButton(text="📦 我的订单", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
@router.message(Command("shop"))
async def show_shop(message: Message) -> None:
    async with SessionFactory() as session:
        products = await list_products(session)
    if not products:
        await message.answer("🛍️ 商店暂时没有可购买的商品。")
        return
    await message.answer(
        "<b>🛍️ TGfakaBOT 商店</b>\n\n请选择商品及支付方式。",
        reply_markup=_shop_keyboard(products),
    )


@router.callback_query(F.data == "my_orders")
async def callback_orders(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_orders(callback.message, callback.from_user.id, page=0)


@router.callback_query(F.data.startswith("orders:"))
async def callback_orders_page(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        page = max(0, int(callback.data.split(":", 1)[1]))
    except (ValueError, IndexError):
        page = 0
    await send_orders(callback.message, callback.from_user.id, page=page)


@router.message(Command("orders"))
async def command_orders(message: Message) -> None:
    await send_orders(message, message.from_user.id, page=0)


async def send_orders(target: Message, telegram_user_id: int, *, page: int) -> None:
    async with SessionFactory() as session:
        orders = await list_user_orders(
            session,
            telegram_user_id,
            offset=page * ORDERS_PAGE_SIZE,
            limit=ORDERS_PAGE_SIZE + 1,
        )
    has_next = len(orders) > ORDERS_PAGE_SIZE
    orders = orders[:ORDERS_PAGE_SIZE]
    if not orders:
        await target.answer("📦 暂无订单。")
        return
    blocks = []
    secret_rows = []
    for order in orders:
        amount_text = (
            f"⭐ {order.total_amount}"
            if order.currency == "XTR"
            else f"{order.total_amount / 100:.2f} {order.currency}"
        )
        blocks.append(
            f"<b>{html.escape(order.product_name)}</b>\n"
            f"订单：<code>{order.public_id}</code>\n"
            f"金额：{amount_text}"
        )
        secret_rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔐 查看 {order.public_id}",
                    callback_data=f"order_secret:{order.public_id}",
                )
            ]
        )
    rows = []
    if page > 0:
        rows.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"orders:{page - 1}"))
    if has_next:
        rows.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"orders:{page + 1}"))
    keyboard_rows = secret_rows + ([rows] if rows else [])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await target.answer(
        f"📦 <b>我的订单 · 第 {page + 1} 页</b>\n\n" + "\n\n".join(blocks),
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("order_secret:"))
async def reveal_order_secret(callback: CallbackQuery) -> None:
    await callback.answer()
    public_id = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        order = await get_user_order(session, callback.from_user.id, public_id)
    if order is None:
        await callback.message.answer("❌ 订单不存在或不属于当前用户。")
        return
    if not order.secret:
        await callback.message.answer("⏳ 该订单尚无可查看的卡密。")
        return
    await callback.message.answer(
        "🔐 <b>订单卡密</b>\n\n"
        f"订单：<code>{order.public_id}</code>\n"
        f"卡密：<code>{html.escape(order.secret)}</code>\n\n"
        "请勿转发；可在确认保存后删除这条 Telegram 消息。"
    )


@router.message(Command("terms"))
async def terms(message: Message) -> None:
    await message.answer(
        "<b>购买条款</b>\n\n"
        "数字商品付款后自动交付。请在付款前确认商品信息；卡密一经展示，除法律要求或商品无效外不支持退款。\n"
        "如发生扣款但未交付，请使用 /paysupport 联系支持。"
    )


@router.message(Command("privacy"))
async def privacy(message: Message) -> None:
    await message.answer(
        "<b>隐私说明</b>\n\n"
        "为完成订单、支付幂等、退款和客服处理，系统会保存 Telegram 用户 ID、用户名/显示名、"
        "订单与支付流水、支付 charge ID 及 Telegram 支付 payload。卡密在数据库中加密保存。\n\n"
        "当您查看卡密时，消息会由 Telegram 保存；请勿向客服发送完整卡密，"
        "并可在确认保存后删除含密消息。"
        "支付及订单记录会按运营和法定留存要求保存。删除或数据问题请使用 /support 联系。"
    )


@router.message(Command("support"))
@router.message(Command("paysupport"))
async def support(message: Message) -> None:
    contact = settings.support_url or "https://t.me/cclchat_bot"
    await message.answer(
        "<b>支付与订单支持</b>\n\n"
        f"请联系：<a href=\"{html.escape(contact, quote=True)}\">{html.escape(contact)}</a>\n"
        "联系时请提供订单号，不要发送完整卡密。"
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy_product(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        product_id = uuid.UUID(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("无效商品。")
        return
    async with SessionFactory() as session:
        try:
            order = await create_order(
                session,
                telegram_user_id=callback.from_user.id,
                username=callback.from_user.username,
                display_name=callback.from_user.full_name,
                product_id=product_id,
            )
        except ShopError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=order.product_name[:32],
            description=order.description[:255],
            payload=order.invoice_payload,
            currency="XTR",
            prices=[LabeledPrice(label=order.product_name[:32], amount=order.total_amount)],
            provider_token="",
            start_parameter=f"order-{order.public_id.lower()}",
        )
    except Exception:
        async with SessionFactory() as session:
            await cancel_unpaid_order(session, order.invoice_payload)
        raise


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    async with SessionFactory() as session:
        ok, error = await validate_pre_checkout(
            session,
            invoice_payload=query.invoice_payload,
            telegram_user_id=query.from_user.id,
            currency=query.currency,
            total_amount=query.total_amount,
        )
    await query.answer(ok=ok, error_message=error or None)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment = message.successful_payment
    try:
        async with SessionFactory() as session:
            receipt = await submit_payment_receipt(
                session,
                telegram_user_id=message.from_user.id,
                invoice_payload=payment.invoice_payload,
                currency=payment.currency,
                total_amount=payment.total_amount,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                provider_payment_charge_id=payment.provider_payment_charge_id,
                raw_payload=payment.model_dump(mode="json"),
            )
        async with SessionFactory() as session:
            delivered = await match_and_fulfill_payment(
                session, charge_id=receipt.charge_id
            )
    except ShopError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}")
        return
    except Exception:
        await message.answer("⚠️ 支付流水已记录，订单正在人工处理中，请联系客服。")
        return
    if delivered.needs_supplier:
        handled = False
        for plugin in get_plugin_registry().plugins:
            if await plugin.fulfill_paid_order(message.bot, delivered):
                handled = True
                break
        if not handled:
            await message.answer("⚠️ 支付成功，但上游供货插件不可用，请联系客服。")
        return
    if not delivered.should_deliver:
        await message.answer(
            "✅ 该支付此前已经处理，未重复发送卡密。可通过 /orders 查看订单。"
        )
        return
    await message.answer(
        "✅ <b>支付成功，商品已交付</b>\n\n"
        f"商品：{html.escape(delivered.product_name)}\n"
        f"订单：<code>{delivered.public_id}</code>\n"
        f"卡密：<code>{html.escape(delivered.secret)}</code>\n\n"
        "请妥善保管卡密，不要转发或公开。"
    )
