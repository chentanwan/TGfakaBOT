import html
import secrets
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.core.config import get_settings
from app.core.i18n import LANGUAGE_NAMES, text
from app.db.session import SessionFactory
from app.plugins.base import PluginPrice, PluginProduct
from app.plugins.registry import get_plugin_registry
from app.services.accounts import (
    ensure_account,
    get_account,
    purchase_with_balance,
    set_account_language,
)
from app.services.api_keys import get_api_credential, rotate_api_key
from app.services.orders import (
    ShopError,
    cancel_unpaid_order,
    create_order,
    get_user_order,
    list_catalog_categories,
    list_product_variants,
    list_products,
    list_user_orders,
    match_and_fulfill_payment,
    submit_payment_receipt,
    validate_pre_checkout,
)
from app.services.topups import list_user_topups, submit_topup

router = Router(name="catalog")
settings = get_settings()
ORDERS_PAGE_SIZE = 5


class CustomQuantity(StatesGroup):
    quantity = State()


class ManualTopup(StatesGroup):
    amount = State()
    proof = State()


def _home_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "products"),
                    callback_data="shop:categories",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=text(language, "balance"), callback_data="account:balance"
                ),
            ],
            [
                InlineKeyboardButton(text=text(language, "topup"), callback_data="account:topup"),
                InlineKeyboardButton(
                    text=text(language, "methods"), callback_data="account:methods"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "profile"), callback_data="account:profile"
                ),
                InlineKeyboardButton(
                    text=text(language, "api"), callback_data="account:api", style="primary"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "language"), callback_data="account:language"
                ),
                InlineKeyboardButton(
                    text=text(language, "support"),
                    callback_data="account:support",
                    style="danger",
                ),
            ],
        ]
    )


def _product_price(product) -> str:
    if "bepusdt" in product.plugin_prices:
        currency, amount = product.plugin_prices["bepusdt"]
        return f"{amount / 100:.2f} {currency}"
    return f"⭐ {product.price_xtr}"


async def _user_language(telegram_user_id: int) -> str:
    async with SessionFactory() as session:
        account = await get_account(session, telegram_user_id)
    return account.language_code if account else settings.bot_language


async def send_home(target: Message, telegram_user_id: int) -> None:
    async with SessionFactory() as session:
        account = await get_account(session, telegram_user_id)
    if account is None:
        await target.answer("Account not found. Send /start again.")
        return
    language = account.language_code
    await target.answer(
        text(
            language,
            "welcome",
            channel=settings.channel_handle,
            support=settings.support_handle,
        )
    )
    await target.answer(
        text(
            language,
            "main_menu",
            balance=f"{account.balance_minor / 100:.2f}",
            points=account.points,
        ),
        reply_markup=_home_keyboard(language),
    )


@router.message(CommandStart())
async def show_home(message: Message) -> None:
    referral = None
    if message.text and len(message.text.split(maxsplit=1)) == 2:
        parameter = message.text.split(maxsplit=1)[1].strip()
        if parameter.lower().startswith("ref_"):
            referral = parameter
    async with SessionFactory() as session:
        await ensure_account(
            session,
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            display_name=message.from_user.full_name,
            telegram_language=message.from_user.language_code,
            referral_code=referral,
        )
    await send_home(message, message.from_user.id)


@router.callback_query(F.data == "shop:home")
async def callback_home(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_home(callback.message, callback.from_user.id)


@router.message(Command("help"))
async def show_help_command(message: Message) -> None:
    async with SessionFactory() as session:
        account = await get_account(session, message.from_user.id)
    language = account.language_code if account else settings.bot_language
    await message.answer(
        "ℹ️ <b>Help</b>\n\n"
        "Use the main menu to browse products, top up your balance and view orders.\n"
        "Purchase flow: product → plan → quantity → confirmation → delivery.\n"
        f"Support: {html.escape(settings.support_handle)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "back"), callback_data="shop:home")]
            ]
        ),
    )


@router.message(Command("shop"))
async def show_shop_command(message: Message) -> None:
    await send_categories(message, message.from_user.id)


@router.callback_query(F.data.startswith("balance:variant:"))
async def balance_variant(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        sku_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return
    from sqlalchemy import select

    from app.db.models import SKU, ProductPrice

    async with SessionFactory() as session:
        sku = await session.get(SKU, sku_id)
        price = await session.scalar(
            select(ProductPrice).where(
                ProductPrice.sku_id == sku_id,
                ProductPrice.provider == "balance",
                ProductPrice.is_active.is_(True),
            )
        )
    if sku is None or price is None:
        await callback.message.answer("Balance purchase is not available for this plan.")
        return
    language = await _user_language(callback.from_user.id)
    await callback.message.answer(
        f"<b>{html.escape(sku.name)}</b>\n"
        f"{text(language, 'unit_price')}: ${price.amount_minor / 100:.2f}\n\n"
        f"{text(language, 'choose_quantity')}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="1", callback_data=f"balance:qty:{sku_id}:1"),
                    InlineKeyboardButton(text="5", callback_data=f"balance:qty:{sku_id}:5"),
                    InlineKeyboardButton(text="10", callback_data=f"balance:qty:{sku_id}:10"),
                ],
                [InlineKeyboardButton(text="Custom", callback_data=f"balance:custom:{sku_id}")],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"shop:product:{sku.product_id}"
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("balance:custom:"))
async def balance_custom_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    sku_id = callback.data.rsplit(":", 1)[1]
    await state.set_state(CustomQuantity.quantity)
    await state.update_data(balance_sku_id=sku_id)
    await callback.message.answer("Enter quantity from 1 to 100:")


@router.message(CustomQuantity.quantity)
async def save_custom_quantity(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        quantity = int((message.text or "").strip())
        sku_id = uuid.UUID(data["balance_sku_id"])
        if quantity < 1 or quantity > 100:
            raise ValueError
    except (ValueError, KeyError):
        await message.answer("Quantity must be a whole number from 1 to 100.")
        return
    await state.clear()
    await send_balance_confirmation(message, message.from_user.id, sku_id, quantity)


@router.callback_query(F.data.startswith("balance:qty:"))
async def balance_quantity(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        _, _, raw_sku, raw_quantity = callback.data.split(":", 3)
        sku_id = uuid.UUID(raw_sku)
        quantity = int(raw_quantity)
    except (ValueError, IndexError):
        return
    await send_balance_confirmation(callback.message, callback.from_user.id, sku_id, quantity)


async def send_balance_confirmation(
    target: Message, telegram_user_id: int, sku_id: uuid.UUID, quantity: int
) -> None:
    from sqlalchemy import select

    from app.db.models import SKU, Product, ProductPrice

    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(SKU, Product, ProductPrice)
                .join(Product, Product.id == SKU.product_id)
                .join(
                    ProductPrice,
                    (ProductPrice.sku_id == SKU.id)
                    & (ProductPrice.provider == "balance")
                    & ProductPrice.is_active.is_(True),
                )
                .where(SKU.id == sku_id)
            )
        ).one_or_none()
        account = await get_account(session, telegram_user_id)
    if row is None or account is None:
        await target.answer("Product is unavailable.")
        return
    sku, product, price = row
    total = price.amount_minor * quantity
    token = secrets.token_urlsafe(12)
    can_buy = account.balance_minor >= total
    rows = []
    if can_buy:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🛒 Buy now",
                    callback_data=f"balance:buy:{sku.id}:{quantity}:{token}",
                    style="success",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Insufficient balance · Top up", callback_data="account:topup"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Back", callback_data=f"balance:variant:{sku.id}")])
    language = await _user_language(telegram_user_id)
    await target.answer(
        f"{text(language, 'confirm_purchase')}\n\n"
        f"Product: {html.escape(product.name)} · {html.escape(sku.name)}\n"
        f"{text(language, 'quantity')}: {quantity}\n"
        f"{text(language, 'unit_price')}: ${price.amount_minor / 100:.2f}\n"
        f"{text(language, 'total')}: ${total / 100:.2f}\n"
        f"Balance: ${account.balance_minor / 100:.2f}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("balance:buy:"))
async def execute_balance_purchase(callback: CallbackQuery) -> None:
    await callback.answer("Processing…")
    try:
        _, _, raw_sku, raw_quantity, token = callback.data.split(":", 4)
        sku_id = uuid.UUID(raw_sku)
        quantity = int(raw_quantity)
        idempotency_key = f"balance:{callback.from_user.id}:{token}"
        async with SessionFactory() as session:
            delivery = await purchase_with_balance(
                session,
                telegram_user_id=callback.from_user.id,
                sku_id=sku_id,
                quantity=quantity,
                idempotency_key=idempotency_key,
                points_per_usd=settings.points_per_usd,
            )
    except ValueError as exc:
        await callback.message.answer(f"❌ {html.escape(str(exc))}")
        return
    delivered = "\n".join(f"<code>{html.escape(value)}</code>" for value in delivery.secrets)
    language = await _user_language(callback.from_user.id)
    await callback.message.answer(
        f"{text(language, 'purchase_completed')}\n\n"
        f"Order: <code>{delivery.public_id}</code>\n"
        f"Product: {html.escape(delivery.product_name)}\n"
        f"{text(language, 'quantity')}: {delivery.quantity}\n"
        f"{text(language, 'unit_price')}: ${delivery.unit_price_minor / 100:.2f}\n"
        f"{text(language, 'total')}: ${delivery.total_amount_minor / 100:.2f}\n\n"
        f"{text(language, 'delivery')}:\n{delivered}\n\n"
        f"{text(language, 'save_delivery')}"
    )


@router.callback_query(F.data == "account:balance")
async def account_balance(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        account = await get_account(session, callback.from_user.id)
    if account is None:
        return
    language = account.language_code
    await callback.message.answer(
        text(
            language,
            "balance_page",
            balance=f"{account.balance_minor / 100:.2f}",
            points=account.points,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "topup"), callback_data="account:topup")],
                [
                    InlineKeyboardButton(
                        text=text(language, "methods"), callback_data="account:methods"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=text(language, "topup_history"), callback_data="account:topups"
                    )
                ],
                [InlineKeyboardButton(text=text(language, "back"), callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data == "account:topups")
async def account_topup_history(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        records = await list_user_topups(session, telegram_user_id=callback.from_user.id)
    lines = ["🧾 <b>Top-up history</b>"]
    if not records:
        lines.append("\nNo top-up records yet.")
    for record in records:
        lines.append(
            f"\n<code>{str(record.id)[:8]}</code> · {html.escape(record.method)} · "
            f"${record.amount_minor / 100:.2f} · {html.escape(record.status)}"
        )
    await callback.message.answer(
        "".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Balance", callback_data="account:balance")]
            ]
        ),
    )


@router.callback_query(F.data == "account:profile")
async def account_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        account = await get_account(session, callback.from_user.id)
    if account is None:
        return
    username = f"@{account.username}" if account.username else "—"
    await callback.message.answer(
        "👤 <b>Your profile</b>\n"
        f"🆔 Telegram ID: <code>{account.telegram_user_id}</code>\n"
        f"👤 Username: {html.escape(username)}\n"
        f"🏅 Tier: {html.escape(account.tier)}\n"
        f"💵 Balance: ${account.balance_minor / 100:.2f}\n"
        f"⭐ Points: {account.points}\n"
        f"📦 Orders: {account.order_count}\n"
        f"💸 Total spent: ${account.total_spent_minor / 100:.2f}\n"
        f"🔑 Referral code: <code>{account.referral_code}</code>\n"
        f"📅 Registered: {account.created_at:%m/%d/%Y}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📦 My orders", callback_data="my_orders"),
                    InlineKeyboardButton(text="👥 Referral", callback_data="account:referral"),
                ],
                [InlineKeyboardButton(text="« Back", callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data == "account:referral")
async def account_referral(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        account = await get_account(session, callback.from_user.id)
    if account is None:
        return
    bot_username = settings.bot_username or "your_bot"
    link = f"https://t.me/{bot_username}?start={account.referral_code}"
    await callback.message.answer(
        "👥 <b>Referral</b>\n\n"
        f"Link: <code>{html.escape(link)}</code>\n"
        f"Invited users: {account.referral_count}\n"
        f"Rewards: ${account.referral_reward_minor / 100:.2f}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Back", callback_data="account:profile")]]
        ),
    )


@router.callback_query(F.data == "account:language")
async def choose_language(callback: CallbackQuery) -> None:
    await callback.answer()
    rows = []
    codes = list(LANGUAGE_NAMES)
    for index in range(0, 6, 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=LANGUAGE_NAMES[code], callback_data=f"account:setlang:{code}"
                )
                for code in codes[index : index + 2]
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=LANGUAGE_NAMES["id"], callback_data="account:setlang:id")]
    )
    async with SessionFactory() as session:
        account = await get_account(session, callback.from_user.id)
    language = account.language_code if account else "en"
    rows.append([InlineKeyboardButton(text=text(language, "back"), callback_data="shop:home")])
    await callback.message.answer(
        text(language, "choose_language"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("account:setlang:"))
async def save_language(callback: CallbackQuery) -> None:
    language = callback.data.rsplit(":", 1)[1]
    async with SessionFactory() as session:
        await set_account_language(
            session, telegram_user_id=callback.from_user.id, language_code=language
        )
    await callback.answer(text(language, "language_updated"))
    await send_home(callback.message, callback.from_user.id)


@router.callback_query(F.data == "account:support")
async def account_support(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "🆘 <b>Need help?</b>\n"
        f"👤 Administrator: {html.escape(settings.administrator_handle)}\n"
        f"🆘 Support: {html.escape(settings.support_handle)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"👤 {settings.administrator_handle} ↗",
                        url=settings.administrator_url,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"🆘 {settings.support_handle} ↗", url=settings.support_url
                    )
                ],
                [InlineKeyboardButton(text="« Back", callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data == "account:methods")
async def account_methods(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "📖 <b>Methods</b>\n\nHow to top up: [configure this text]\n\n"
        "How to buy: choose a product, quantity and payment method.\n\n"
        "After delivery: save your digital product securely.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Back", callback_data="shop:home")]]
        ),
    )


@router.callback_query(F.data == "account:topup")
async def account_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "💳 <b>Top-up methods</b>\nChoose a payment method:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔶 Binance Pay", callback_data="topup:start:binance")],
                [InlineKeyboardButton(text="🤖 CryptoBot", callback_data="topup:start:cryptobot")],
                [
                    InlineKeyboardButton(
                        text="₮ USDT BEP20 (BSC)", callback_data="topup:start:bep20"
                    )
                ],
                [InlineKeyboardButton(text="« Back", callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("topup:start:"))
async def start_manual_topup(callback: CallbackQuery, state: FSMContext) -> None:
    method = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    if method == "cryptobot":
        await callback.message.answer(
            "Open CryptoBot automatic invoice flow:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🤖 Create CryptoBot invoice",
                            callback_data="plugin:cryptobot:topup",
                        )
                    ],
                    [InlineKeyboardButton(text="« Back", callback_data="account:topup")],
                ]
            ),
        )
        return
    instructions = (
        f"Send USDT to Binance Pay UID: <code>{html.escape(settings.binance_pay_uid)}</code>. "
        "Then submit the Order ID."
        if method == "binance"
        else f"Send USDT on BSC to: <code>{html.escape(settings.usdt_bep20_address)}</code>. "
        "Then submit the transaction hash."
    )
    await state.update_data(topup_method=method)
    await state.set_state(ManualTopup.amount)
    await callback.message.answer(
        f"💳 <b>{'Binance Pay' if method == 'binance' else 'USDT BEP20 (BSC)'}</b>\n"
        f"{instructions}\n\n"
        f"💵 Minimum: ${settings.minimum_topup_minor / 100:.2f}\n"
        "How much did you send? Enter the USD amount:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✖ Cancel", callback_data="topup:cancel", style="danger"
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data == "topup:cancel")
async def cancel_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Top-up cancelled")
    await state.clear()
    await send_home(callback.message, callback.from_user.id)


@router.message(ManualTopup.amount)
async def manual_topup_amount(message: Message, state: FSMContext) -> None:
    try:
        amount_minor = round(float((message.text or "").strip()) * 100)
        if amount_minor < settings.minimum_topup_minor:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Enter a valid amount of at least ${settings.minimum_topup_minor / 100:.2f}."
        )
        return
    await state.update_data(topup_amount_minor=amount_minor)
    await state.set_state(ManualTopup.proof)
    await message.answer("Send the payment Order ID or transaction hash:")


@router.message(ManualTopup.proof)
async def manual_topup_proof(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        async with SessionFactory() as session:
            record = await submit_topup(
                session,
                telegram_user_id=message.from_user.id,
                method=data["topup_method"],
                amount_minor=int(data["topup_amount_minor"]),
                proof=message.text or "",
            )
    except (ValueError, KeyError) as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        f"✅ Submitted and waiting for administrator review.\nReference: <code>{record.id}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Main menu", callback_data="shop:home")]]
        ),
    )


@router.callback_query(F.data == "account:api")
async def account_api(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        account = await get_account(session, callback.from_user.id)
        credential = await get_api_credential(session, callback.from_user.id)
    if account is None:
        return
    key_text = (
        f"<code>{html.escape(credential.prefix)}••••••••</code>"
        if credential.prefix
        else "Not generated"
    )
    last_used = (
        credential.last_used_at.strftime("%m/%d/%Y %H:%M UTC")
        if credential.last_used_at
        else "Never"
    )
    await callback.message.answer(
        "🔗 <b>Customer API</b>\n\n"
        f"API Key: {key_text}\n"
        f"Balance: ${account.balance_minor / 100:.2f}\n"
        f"Requests: {credential.request_count}\n"
        f"Last used: {last_used}\n"
        f"Documentation: {html.escape(settings.api_docs_url)}\n\n"
        "For security, the complete key is shown only immediately after generation.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Regenerate Key", callback_data="account:api:rotate"
                    )
                ],
                [InlineKeyboardButton(text="« Back", callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data == "account:api:rotate")
async def account_api_rotate(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionFactory() as session:
        api_key = await rotate_api_key(session, callback.from_user.id)
    await callback.message.answer(
        "🔑 <b>Your new API Key</b>\n\n"
        f"<code>{html.escape(api_key)}</code>\n\n"
        "Save it now. It will not be shown in full again, and the previous key is invalid.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« API", callback_data="account:api")]]
        ),
    )


@router.callback_query(F.data == "shop:categories")
async def callback_categories(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_categories(callback.message, callback.from_user.id)


async def send_categories(target: Message, telegram_user_id: int | None = None) -> None:
    async with SessionFactory() as session:
        categories = await list_catalog_categories(session)
    language = await _user_language(telegram_user_id) if telegram_user_id else settings.bot_language
    if not categories:
        await target.answer(
            "🛍 No products are available right now.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Home", callback_data="shop:home")]]
            ),
        )
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"📁 {category.name} ({category.product_count})",
                callback_data=f"shop:category:{category.id or 'all'}",
            )
        ]
        for category in categories
    ]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="shop:home")])
    await target.answer(
        text(language, "choose_category"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("shop:category:"))
async def callback_category(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        raw_category = callback.data.rsplit(":", 1)[1]
        category_id = None if raw_category == "all" else uuid.UUID(raw_category)
    except (ValueError, IndexError):
        await callback.message.answer("Invalid category.")
        return
    async with SessionFactory() as session:
        products = await list_products(session)
    products = [product for product in products if product.category_id == category_id]
    if not products:
        await callback.message.answer("No products in this category.")
        return
    rows = []
    language = await _user_language(callback.from_user.id)
    for product in products:
        available = product.stock > 0 or product.supplier_stock
        stock_text = text(language, "in_stock") if available else text(language, "out_of_stock")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(f"{product.name} · {_product_price(product)} · {stock_text}"),
                    callback_data=f"shop:product:{product.id}",
                    style="success" if available else "danger",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=text(language, "back"), callback_data="shop:categories")]
    )
    await callback.message.answer(
        f"📦 <b>{html.escape(products[0].category_name or text(language, 'products'))}</b>\n"
        f"{len(products)} · {text(language, 'choose_product')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("shop:product:"))
async def callback_product_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Invalid product.")
        return
    async with SessionFactory() as session:
        products = await list_products(session)
    product = next((item for item in products if item.id == product_id), None)
    if product is None:
        await callback.message.answer("Product is unavailable.")
        return
    variants = []
    async with SessionFactory() as session:
        variants = await list_product_variants(session, product.id)
    rows = []
    if len(variants) > 1:
        for variant in variants:
            available_variant = variant.stock > 0 or variant.supplier_stock
            price_text = (
                f"${variant.balance_price_minor / 100:.2f}"
                if variant.balance_price_minor is not None
                else _product_price(variant)
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{variant.sku_name} | {price_text} | "
                        f"{'🟢' if available_variant else '🔴'} {variant.stock}",
                        callback_data=f"balance:variant:{variant.sku_id}",
                        style="success" if available_variant else "danger",
                    )
                ]
            )
    elif variants and variants[0].balance_price_minor is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💵 Buy with balance · ${variants[0].balance_price_minor / 100:.2f}",
                    callback_data=f"balance:variant:{variants[0].sku_id}",
                    style="success" if variants[0].stock > 0 else "danger",
                )
            ]
        )
    if product.price_xtr and product.stock > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⭐ Buy · {product.price_xtr} XTR",
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
    available = product.stock > 0 or product.supplier_stock
    if available:
        for plugin in get_plugin_registry().plugins:
            button = plugin.catalog_button(plugin_product)
            if button is not None:
                rows.append([button])
    raw_category = str(product.category_id) if product.category_id else "all"
    rows.append(
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"shop:category:{raw_category}")]
    )
    delivery = "Instant delivery" if available else "Out of stock"
    await callback.message.answer(
        f"<b>{html.escape(product.name)}</b>\n\n"
        f"{html.escape(product.description or 'No description available.')}\n\n"
        f"💵 Price: {_product_price(product)}\n"
        f"⚡ Delivery: {delivery}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "shop:account")
async def callback_account(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "👤 <b>My account</b>\n\n"
        f"Name: {html.escape(callback.from_user.full_name)}\n"
        f"Telegram ID: <code>{callback.from_user.id}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📦 My orders", callback_data="my_orders")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="shop:home")],
            ]
        ),
    )


@router.callback_query(F.data == "shop:help")
async def callback_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "ℹ️ <b>Help</b>\n\n"
        "1. Choose a product.\n"
        "2. Select an available payment method.\n"
        "3. Complete payment before the order expires.\n"
        "4. Your digital product will be delivered in Telegram.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Home", callback_data="shop:home")]]
        ),
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
        status_labels = {
            "pending_payment": "Awaiting payment",
            "payment_processing": "Processing",
            "paid": "Paid",
            "fulfilled": "Delivered",
            "expired": "Expired",
            "payment_exception": "Needs support",
            "refund_pending": "Refund pending",
            "refunded": "Refunded",
        }
        blocks.append(
            f"<b>{html.escape(order.product_name)}</b>\n"
            f"Order: <code>{order.public_id}</code>\n"
            f"Amount: {amount_text}\n"
            f"Status: {status_labels.get(order.status, order.status)}"
        )
        secret_rows.append(
            [
                InlineKeyboardButton(
                    text=f"View {order.public_id}",
                    callback_data=f"order_detail:{order.public_id}",
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
        f"📦 <b>My orders · Page {page + 1}</b>\n\n" + "\n\n".join(blocks),
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("order_detail:"))
async def order_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    public_id = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        order = await get_user_order(session, callback.from_user.id, public_id)
    if order is None:
        await callback.message.answer("Order not found.")
        return
    amount = (
        f"⭐ {order.total_amount}"
        if order.currency == "XTR"
        else f"{order.total_amount / 100:.2f} {order.currency}"
    )
    rows = []
    if order.secret:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 View delivered product",
                    callback_data=f"order_secret:{order.public_id}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="📦 My orders", callback_data="my_orders")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="shop:home")],
        ]
    )
    await callback.message.answer(
        "🧾 <b>Order details</b>\n\n"
        f"Product: {html.escape(order.product_name)}\n"
        f"Order: <code>{order.public_id}</code>\n"
        f"Amount: {amount}\n"
        f"Status: {html.escape(order.status)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
        f'请联系：<a href="{html.escape(contact, quote=True)}">{html.escape(contact)}</a>\n'
        "联系时请提供订单号，不要发送完整卡密。"
    )


@router.callback_query(F.data.startswith("buy:"))
async def confirm_stars_purchase(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        product_id = uuid.UUID(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Invalid product.")
        return
    async with SessionFactory() as session:
        products = await list_products(session)
    product = next((item for item in products if item.id == product_id), None)
    if product is None or product.stock < 1:
        await callback.message.answer("This product is unavailable.")
        return
    await callback.message.answer(
        "🧾 <b>Confirm purchase</b>\n\n"
        f"Product: {html.escape(product.name)}\n"
        f"Price: ⭐ {product.price_xtr} XTR\n"
        "Delivery: Instant\n\nContinue to Telegram Stars payment?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Confirm and pay", callback_data=f"buy_confirm:{product.id}"
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Back", callback_data=f"shop:product:{product.id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("buy_confirm:"))
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
            delivered = await match_and_fulfill_payment(session, charge_id=receipt.charge_id)
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
        await message.answer("✅ 该支付此前已经处理，未重复发送卡密。可通过 /orders 查看订单。")
        return
    await message.answer(
        "✅ <b>支付成功，商品已交付</b>\n\n"
        f"商品：{html.escape(delivered.product_name)}\n"
        f"订单：<code>{delivered.public_id}</code>\n"
        f"卡密：<code>{html.escape(delivered.secret)}</code>\n\n"
        "请妥善保管卡密，不要转发或公开。"
    )
