import asyncio
import html
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.plugins.registry import get_plugin_registry
from app.services.admin import (
    adjust_user_balance,
    create_product,
    find_admin_user,
    import_cards,
    list_admin_products,
    list_broadcast_user_ids,
    toggle_product,
)
from app.services.admin_access import is_admin
from app.services.audit import record_admin_action
from app.services.topups import list_pending_topups, review_topup

router = Router(name="admin")
settings = get_settings()


class NewProduct(StatesGroup):
    name = State()
    description = State()
    price = State()


class CardImport(StatesGroup):
    content = State()


class TopupRejection(StatesGroup):
    reason = State()


class UserLookup(StatesGroup):
    telegram_id = State()


class BalanceAdjustment(StatesGroup):
    amount = State()


class AdminBroadcast(StatesGroup):
    content = State()
    confirmation = State()


class PaymentMethodEdit(StatesGroup):
    value = State()


def _back_markup(callback_data: str, label: str = "« 返回") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback_data)]]
    )


def _usd_price(amount_minor: int | None) -> str:
    return f"${amount_minor / 100:.2f}" if amount_minor is not None else "未设置"


def admin_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📦 商品库存", callback_data="adm:list"),
            InlineKeyboardButton(text="🏭 供应商", callback_data="adm:suppliers"),
        ],
        [
            InlineKeyboardButton(text="💰 价格设置", callback_data="plugin:prices:list"),
            InlineKeyboardButton(text="⚙️ 支付设置", callback_data="adm:usdt:health"),
        ],
        [
            InlineKeyboardButton(text="₮ USDT 审核", callback_data="adm:usdt:list"),
            InlineKeyboardButton(text="💳 充值审核", callback_data="adm:topups:list"),
        ],
        [InlineKeyboardButton(text="🛡 管理员管理", callback_data="plugin:adminmgr:list")],
    ]
    for plugin in get_plugin_registry().plugins:
        rows.extend(plugin.admin_home_buttons())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("admin"))
async def admin_home(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("⚙️ <b>TGfakaBOT 管理后台</b>", reply_markup=admin_keyboard())


@router.callback_query(F.data == "adm:suppliers")
async def supplier_admin_home(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    plugins = get_plugin_registry().supplier_plugins
    if not plugins:
        await callback.message.answer(
            "当前没有已加载的供应商插件。", reply_markup=_back_markup("adm:home", "« 管理后台")
        )
        return
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["🏭 <b>供应商管理</b>"]
    for plugin in plugins:
        display_name = getattr(plugin, "supplier_display_name", plugin.name)
        lines.append(f"\n• {html.escape(display_name)} · <code>{html.escape(plugin.name)}</code>")
        buttons = getattr(plugin, "supplier_admin_buttons", lambda: [])()
        rows.extend(buttons)
    rows.append([InlineKeyboardButton(text="« 返回管理后台", callback_data="adm:home")])
    await callback.message.answer(
        "".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data == "adm:home")
async def admin_home_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await callback.message.answer("⚙️ <b>TGfakaBOT 管理后台</b>", reply_markup=admin_keyboard())


@router.callback_query(F.data == "adm:new")
async def new_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await state.set_state(NewProduct.name)
    await callback.message.answer("请输入商品名称，发送 /cancel 取消：")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("已取消。")


@router.message(NewProduct.name)
async def new_product_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 200:
        await message.answer("名称长度应为 1–200 字符。")
        return
    await state.update_data(name=name)
    await state.set_state(NewProduct.description)
    await message.answer("请输入商品描述：")


@router.message(NewProduct.description)
async def new_product_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) > 3000:
        await message.answer("描述不能超过 3000 字符。")
        return
    await state.update_data(description=description)
    await state.set_state(NewProduct.price)
    await message.answer("请输入美元价格，例如 4.99：")


@router.message(NewProduct.price)
async def new_product_price(message: Message, state: FSMContext) -> None:
    try:
        decimal_price = Decimal((message.text or "").strip())
        if not decimal_price.is_finite() or decimal_price <= 0:
            raise ValueError
        price = int((decimal_price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation):
        await message.answer("请输入有效的美元价格，例如 4.99。")
        return
    data = await state.get_data()
    async with SessionFactory() as session:
        product = await create_product(
            session,
            name=data["name"],
            description=data["description"],
            price_usd_minor=price,
            admin_telegram_id=message.from_user.id,
        )
    await state.clear()
    await message.answer(
        f"✅ 商品已创建：<b>{html.escape(product.name)}</b>\n下一步请在商品列表中导入卡密。",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "adm:list")
async def admin_product_list(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        products = await list_admin_products(session)
    if not products:
        await callback.message.answer("暂无商品。", reply_markup=admin_keyboard())
        return
    rows = []
    lines = ["📦 <b>商品与库存</b>", "\n请选择商品查看库存和管理操作："]
    for product in products:
        icon = "✅" if product.status == "active" else "⏸"
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{icon} {product.name[:22]} · "
                        f"{_usd_price(product.price_usd_minor)} · 库存{product.available}"
                    ),
                    callback_data=f"adm:product:{product.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="➕ 新增商品", callback_data="adm:new"),
            InlineKeyboardButton(text="« 管理后台", callback_data="adm:home"),
        ]
    )
    await callback.message.answer(
        "".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("adm:product:"))
async def admin_product_detail(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("参数错误", show_alert=True)
        return
    async with SessionFactory() as session:
        products = await list_admin_products(session)
    product = next((item for item in products if item.id == product_id), None)
    if product is None:
        await callback.answer("商品不存在", show_alert=True)
        return
    icon = "✅" if product.status == "active" else "⏸"
    rows = [
        [
            InlineKeyboardButton(text="📥 导入卡密", callback_data=f"adm:cards:{product.id}"),
            InlineKeyboardButton(text="⏯ 启停商品", callback_data=f"adm:toggle:{product.id}"),
        ]
    ]
    plugin_buttons = [
        button
        for plugin in get_plugin_registry().plugins
        if (button := plugin.admin_product_button(product.id)) is not None
    ]
    for index in range(0, len(plugin_buttons), 2):
        rows.append(plugin_buttons[index : index + 2])
    rows.append([InlineKeyboardButton(text="« 商品与库存", callback_data="adm:list")])
    await callback.answer()
    await callback.message.answer(
        "📦 <b>商品管理</b>\n\n"
        f"商品：{html.escape(product.name)}\n"
        f"状态：{icon} {html.escape(product.status)}\n"
        f"美元价格：{_usd_price(product.price_usd_minor)}\n"
        f"本地库存：{product.available}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("adm:cards:"))
async def cards_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await callback.answer()
    await state.update_data(product_id=str(product_id))
    await state.set_state(CardImport.content)
    await callback.message.answer("请粘贴卡密，每行一条；单次最多 500 条。发送 /cancel 取消。")


@router.message(CardImport.content)
async def cards_import(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        product_id = uuid.UUID(data["product_id"])
        async with SessionFactory() as session:
            result = await import_cards(
                session,
                product_id=product_id,
                secrets=(message.text or "").splitlines(),
                admin_telegram_id=message.from_user.id,
            )
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        f"✅ 导入完成\n成功：{result.inserted}\n重复/跳过：{result.duplicates}",
        reply_markup=admin_keyboard(),
    )
    if settings.restock_broadcast_chat_id and result.inserted:
        await message.bot.send_message(
            settings.restock_broadcast_chat_id,
            f"📦 <b>{html.escape(result.product_name)}</b>\n\n"
            f"➕ Added: {result.inserted}\n"
            f"📦 Current stock: {result.current_stock}\n"
            f"💵 Unit price: {_usd_price(result.unit_price_usd_minor)}",
        )


@router.callback_query(F.data.startswith("adm:toggle:"))
async def product_toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        async with SessionFactory() as session:
            status = await toggle_product(
                session, product_id, admin_telegram_id=callback.from_user.id
            )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"已切换为 {status.value}", show_alert=True)


@router.callback_query(F.data == "adm:usdt:health")
async def usdt_payment_health(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.payment_settings import list_payment_methods
    from app.services.usdt_verification import automatic_verification_available

    await callback.answer()
    async with SessionFactory() as session:
        methods = await list_payment_methods(session, settings)
    lines = [
        "⚙️ <b>支付设置</b>",
        f"\n自动核验总开关：{'开启' if settings.usdt_auto_verify_enabled else '关闭'}",
        f"\nEVM 确认数：{settings.usdt_evm_confirmations}",
        f" · TRON 确认数：{settings.usdt_tron_confirmations}",
    ]
    method_buttons = []
    for method in methods:
        auto_verify = automatic_verification_available(settings, method.id)
        destination = (
            _mask_payment_destination(method.destination) if method.destination else "未配置"
        )
        lines.append(
            f"\n\n<b>{html.escape(method.label)}</b>"
            f"\n状态：{'✅ 开启' if method.enabled else '⛔ 关闭'}"
            f" · 显示：{'是' if method.available else '否'}"
            f" · 自动核验：{'可用' if auto_verify else '不可用'}"
            f"\n说明：{html.escape(method.description or '未设置')}"
            f"\n最低充值：${method.minimum_topup_minor / 100:.2f}"
            f" · 排序：{method.sort_order}"
            f"\n收款信息：<code>{html.escape(destination)}</code>"
        )
        method_buttons.append(
            InlineKeyboardButton(
                text=f"{'✅' if method.enabled else '⛔'} {method.label}",
                callback_data=f"adm:pay:view:{method.id}",
            )
        )
    rows = [method_buttons[index : index + 2] for index in range(0, len(method_buttons), 2)]
    rows.append([InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")])
    await callback.message.answer(
        "".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("adm:pay:view:"))
async def payment_method_view(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.payment_settings import get_payment_method

    method_id = callback.data.rsplit(":", 1)[1]
    async with SessionFactory() as session:
        method = await get_payment_method(session, settings, method_id)
    if method is None:
        await callback.answer("支付方式不存在", show_alert=True)
        return
    await callback.answer()
    fields = (
        ("名称", "label"),
        ("说明", "description"),
        ("收款信息", "destination"),
        ("最低金额", "minimum"),
        ("排序", "sort"),
    )
    field_buttons = [
        InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"adm:pay:edit:{field}:{method.id}")
        for label, field in fields
    ]
    rows = [field_buttons[:2], field_buttons[2:4], field_buttons[4:]]
    rows.append(
        [
            InlineKeyboardButton(
                text="⛔ 关闭" if method.enabled else "✅ 开启",
                callback_data=f"adm:pay:toggle:{method.id}",
                style="danger" if method.enabled else "success",
            ),
            InlineKeyboardButton(text="« 支付设置", callback_data="adm:usdt:health"),
        ]
    )
    masked_destination = (
        html.escape(_mask_payment_destination(method.destination))
        if method.destination
        else "未设置"
    )
    await callback.message.answer(
        f"<b>{html.escape(method.label)}</b>\n"
        f"状态：{'开启' if method.enabled else '关闭'}\n"
        f"说明：{html.escape(method.description or '未设置')}\n"
        f"收款信息：<code>{masked_destination}</code>\n"
        f"最低充值：${method.minimum_topup_minor / 100:.2f}\n"
        f"排序：{method.sort_order}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("adm:pay:toggle:"))
async def payment_method_toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.payment_settings import toggle_payment_method

    method_id = callback.data.rsplit(":", 1)[1]
    try:
        async with SessionFactory() as session:
            method = await toggle_payment_method(session, settings, method_id)
            await record_admin_action(
                session,
                admin_telegram_id=callback.from_user.id,
                action="payment_method_toggle",
                target_type="payment_method",
                target_id=method_id,
                details={"enabled": method.enabled},
            )
            await session.commit()
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("已开启" if method.enabled else "已关闭", show_alert=True)
    await payment_method_view(callback)


@router.callback_query(F.data.startswith("adm:pay:edit:"))
async def payment_method_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, field, method_id = callback.data.split(":", 4)
    except ValueError:
        await callback.answer("无效请求", show_alert=True)
        return
    prompts = {
        "label": "请输入新的支付方式名称：",
        "description": "请输入新的用户说明：",
        "destination": "请输入新的收款 UID 或地址：",
        "minimum": "请输入最低充值金额（USD，例如 1.50）：",
        "sort": "请输入排序数字（越小越靠前）：",
    }
    if field not in prompts:
        await callback.answer("不支持的字段", show_alert=True)
        return
    await callback.answer()
    await state.update_data(payment_method_id=method_id, payment_field=field)
    await state.set_state(PaymentMethodEdit.value)
    await callback.message.answer(prompts[field])


@router.message(PaymentMethodEdit.value)
async def payment_method_edit_value(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    from app.services.payment_settings import update_payment_method

    data = await state.get_data()
    method_id = str(data.get("payment_method_id") or "")
    field = str(data.get("payment_field") or "")
    raw = (message.text or "").strip()
    field_map = {"minimum": "minimum_topup_minor", "sort": "sort_order"}
    target = field_map.get(field, field)
    try:
        value = (
            round(float(raw) * 100) if field == "minimum" else int(raw) if field == "sort" else raw
        )
        async with SessionFactory() as session:
            method = await update_payment_method(session, settings, method_id, target, value)
            await record_admin_action(
                session,
                admin_telegram_id=message.from_user.id,
                action="payment_method_edit",
                target_type="payment_method",
                target_id=method_id,
                details={"field": target},
            )
            await session.commit()
    except (ValueError, TypeError) as exc:
        await message.answer(f"❌ {html.escape(str(exc) or '输入格式无效')}，请重新输入：")
        return
    await state.clear()
    await message.answer(
        f"✅ 已更新 {html.escape(method.label)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="返回支付方式", callback_data=f"adm:pay:view:{method.id}"
                    )
                ]
            ]
        ),
    )


def _verification_configuration(method_id: str) -> tuple[bool, bool]:
    if method_id == "usdt_bep20":
        return bool(settings.usdt_bep20_rpc_url), bool(settings.usdt_bep20_token_contract)
    if method_id == "usdt_arbitrum":
        return bool(settings.usdt_arbitrum_rpc_url), bool(settings.usdt_arbitrum_token_contract)
    if method_id == "usdt_trc20":
        return bool(settings.usdt_tron_api_url), bool(settings.usdt_trc20_token_contract)
    return False, False


def _mask_payment_destination(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


@router.callback_query(F.data == "adm:usdt:list")
async def pending_usdt_orders(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.plugin_payments import list_manual_usdt_reviews

    await callback.answer()
    async with SessionFactory() as session:
        records = await list_manual_usdt_reviews(session)
    if not records:
        await callback.message.answer(
            "当前没有待审核 USDT 商品订单。", reply_markup=_back_markup("adm:home", "« 管理后台")
        )
        return
    for record in records:
        await callback.message.answer(
            "₮ <b>待审核 USDT 商品订单</b>\n"
            f"订单：<code>{record.public_id}</code>\n"
            f"用户：<code>{record.telegram_user_id}</code>\n"
            f"商品：{html.escape(record.product_name)}\n"
            f"网络：{html.escape(record.method_label)}\n"
            f"收款目标：<code>{html.escape(record.destination)}</code>\n"
            f"金额：${record.amount_minor / 100:.2f}\n"
            f"凭证：<code>{html.escape(record.proof)}</code>\n"
            f"状态：{'⚠️ 已超过 24 小时' if record.overdue else '待审核'}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ 确认到账",
                            callback_data=f"adm:usdt:ask:a:{record.payment_id}",
                            style="success",
                        ),
                        InlineKeyboardButton(
                            text="❌ 拒绝",
                            callback_data=f"adm:usdt:ask:r:{record.payment_id}",
                            style="danger",
                        ),
                    ],
                    [InlineKeyboardButton(text="« 返回审核列表", callback_data="adm:usdt:list")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("adm:usdt:ask:"))
async def ask_usdt_review(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, action, raw_payment_id = callback.data.split(":", 4)
        payment_id = uuid.UUID(raw_payment_id)
        if action not in {"a", "r"}:
            raise ValueError
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    from redis.asyncio import Redis

    from app.services.admin_confirmations import create_confirmation

    redis = Redis.from_url(settings.redis_url)
    try:
        token = await create_confirmation(
            redis,
            admin_id=callback.from_user.id,
            action="usdt_approve" if action == "a" else "usdt_reject",
            order_id=payment_id,
        )
    finally:
        await redis.aclose()
    label = "确认到账并交付" if action == "a" else "拒绝并释放库存"
    await callback.answer()
    await callback.message.answer(
        f"⚠️ <b>{label}</b>\n\n确认令牌 5 分钟内有效且只能使用一次。",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=f"ud:d:{action}:{payment_id}:{token}",
                        style="success" if action == "a" else "danger",
                    )
                ],
                [InlineKeyboardButton(text="« 返回审核列表", callback_data="adm:usdt:list")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("ud:d:a:"))
async def approve_usdt_order(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.plugin_payments import (
        PluginPaymentError,
        approve_manual_usdt_payment,
    )

    try:
        _, _, _, raw_payment_id, token = callback.data.split(":", 4)
        payment_id = uuid.UUID(raw_payment_id)
        from redis.asyncio import Redis

        from app.services.admin_confirmations import consume_confirmation

        redis = Redis.from_url(settings.redis_url)
        try:
            confirmed = await consume_confirmation(
                redis,
                token=token,
                admin_id=callback.from_user.id,
                action="usdt_approve",
                order_id=payment_id,
            )
        finally:
            await redis.aclose()
        if not confirmed:
            await callback.answer("确认已失效或已使用", show_alert=True)
            return
        async with SessionFactory() as session:
            delivery, duplicate = await approve_manual_usdt_payment(
                session,
                payment_id=payment_id,
                admin_telegram_id=callback.from_user.id,
            )
    except (ValueError, PluginPaymentError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("该记录已处理" if duplicate else "已确认到账", show_alert=True)
    if duplicate:
        return
    if delivery.needs_supplier and delivery.order_id:
        for plugin in get_plugin_registry().supplier_plugins:
            if await plugin.fulfill_paid_order(callback.bot, delivery):
                return
    elif delivery.should_deliver:
        await callback.bot.send_message(
            delivery.telegram_user_id,
            "✅ <b>USDT payment confirmed</b>\n\n"
            f"Order: <code>{delivery.public_id}</code>\n"
            f"Product: {html.escape(delivery.product_name)}\n"
            f"Secret: <code>{html.escape(delivery.secret)}</code>",
        )
    elif delivery.exception_message:
        await callback.bot.send_message(
            delivery.telegram_user_id, html.escape(delivery.exception_message)
        )


@router.callback_query(F.data.startswith("ud:d:r:"))
async def reject_usdt_order(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    from app.services.plugin_payments import (
        PluginPaymentError,
        reject_manual_usdt_payment,
    )

    try:
        _, _, _, raw_payment_id, token = callback.data.split(":", 4)
        payment_id = uuid.UUID(raw_payment_id)
        from redis.asyncio import Redis

        from app.services.admin_confirmations import consume_confirmation

        redis = Redis.from_url(settings.redis_url)
        try:
            confirmed = await consume_confirmation(
                redis,
                token=token,
                admin_id=callback.from_user.id,
                action="usdt_reject",
                order_id=payment_id,
            )
        finally:
            await redis.aclose()
        if not confirmed:
            await callback.answer("确认已失效或已使用", show_alert=True)
            return
        async with SessionFactory() as session:
            telegram_user_id, public_id, duplicate = await reject_manual_usdt_payment(
                session,
                payment_id=payment_id,
                admin_telegram_id=callback.from_user.id,
            )
    except (ValueError, PluginPaymentError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("该记录已处理" if duplicate else "已拒绝并释放库存", show_alert=True)
    if not duplicate:
        await callback.bot.send_message(
            telegram_user_id,
            f"❌ USDT payment proof for order <code>{public_id}</code> was rejected.",
        )


@router.callback_query(F.data == "adm:topups:list")
async def pending_topups(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        records = await list_pending_topups(session)
    if not records:
        await callback.message.answer(
            "当前没有待审核充值。", reply_markup=_back_markup("adm:home", "« 管理后台")
        )
        return
    for record in records:
        await callback.message.answer(
            "💳 <b>待审核充值</b>\n"
            f"用户：{html.escape(record.display_name)} (<code>{record.telegram_user_id}</code>)\n"
            f"方式：{html.escape(record.method)}\n"
            f"金额：${record.amount_minor / 100:.2f}\n"
            f"凭证：<code>{html.escape(record.proof)}</code>\n"
            f"状态：{'⚠️ 已超过 24 小时' if record.overdue else '待审核'}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ 通过",
                            callback_data=f"adm:topup:approve:{record.id}",
                            style="success",
                        ),
                        InlineKeyboardButton(
                            text="❌ 拒绝",
                            callback_data=f"adm:topup:reject:{record.id}",
                            style="danger",
                        ),
                    ],
                    [InlineKeyboardButton(text="« 返回审核列表", callback_data="adm:topups:list")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("adm:topup:approve:"))
async def approve_topup(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        topup_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        async with SessionFactory() as session:
            record, telegram_user_id, duplicate = await review_topup(
                session, topup_id=topup_id, admin_telegram_id=callback.from_user.id, approve=True
            )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("该记录已处理" if duplicate else "充值已到账", show_alert=True)
    if not duplicate:
        await callback.bot.send_message(
            telegram_user_id,
            f"✅ Top-up approved: ${record.amount_minor / 100:.2f} has been added to your balance.",
        )


@router.callback_query(F.data.startswith("adm:topup:reject:"))
async def reject_topup_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        topup_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await state.set_state(TopupRejection.reason)
    await state.update_data(reject_topup_id=str(topup_id))
    await callback.answer()
    await callback.message.answer("请输入拒绝原因；发送 /cancel 取消：")


@router.message(TopupRejection.reason)
async def reject_topup_reason(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    reason = (message.text or "").strip()
    if not 1 <= len(reason) <= 500:
        await message.answer("拒绝原因长度应为 1–500 字符。")
        return
    data = await state.get_data()
    try:
        topup_id = uuid.UUID(data["reject_topup_id"])
        async with SessionFactory() as session:
            record, telegram_user_id, duplicate = await review_topup(
                session,
                topup_id=topup_id,
                admin_telegram_id=message.from_user.id,
                approve=False,
                rejection_reason=reason,
            )
    except (ValueError, KeyError) as exc:
        await message.answer(f"处理失败：{html.escape(str(exc))}")
        return
    await state.clear()
    await message.answer("该记录已处理" if duplicate else "充值已拒绝。")
    if not duplicate:
        await message.bot.send_message(
            telegram_user_id,
            f"❌ Top-up rejected: ${record.amount_minor / 100:.2f}.\nReason: {html.escape(reason)}",
        )


@router.callback_query(F.data == "adm:user:find")
async def admin_user_find_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await state.set_state(UserLookup.telegram_id)
    await callback.message.answer("请输入用户 Telegram 数字 ID：")


@router.message(UserLookup.telegram_id)
async def admin_user_find(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        telegram_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("请输入有效数字 ID。")
        return
    async with SessionFactory() as session:
        user = await find_admin_user(session, telegram_id)
    if user is None:
        await message.answer("用户不存在。")
        return
    await state.clear()
    username = f"@{user.username}" if user.username else "—"
    await message.answer(
        "👤 <b>用户资料</b>\n"
        f"ID：<code>{user.telegram_user_id}</code>\n"
        f"用户名：{html.escape(username)}\n"
        f"余额：${user.balance_minor / 100:.2f}\n"
        f"积分：{user.points}\n"
        f"订单：{user.order_count}\n"
        f"总消费：${user.total_spent_minor / 100:.2f}\n"
        f"状态：{html.escape(user.status)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 调整余额", callback_data=f"adm:user:balance:{telegram_id}"
                    )
                ],
                [InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:user:balance:"))
async def admin_balance_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    telegram_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(BalanceAdjustment.amount)
    await state.update_data(adjust_user_id=telegram_id)
    await callback.answer()
    await callback.message.answer("输入调整金额（USD），例如 10 或 -5.50：")


@router.message(BalanceAdjustment.amount)
async def admin_balance_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    try:
        delta_minor = round(float((message.text or "").strip()) * 100)
        telegram_id = int(data["adjust_user_id"])
        async with SessionFactory() as session:
            balance = await adjust_user_balance(
                session,
                telegram_user_id=telegram_id,
                delta_minor=delta_minor,
                admin_telegram_id=message.from_user.id,
            )
    except (ValueError, KeyError) as exc:
        await message.answer(f"调整失败：{html.escape(str(exc))}")
        return
    await state.clear()
    await message.answer(f"✅ 调整完成，当前余额：${balance / 100:.2f}")
    await message.bot.send_message(
        telegram_id,
        f"💰 Your balance was adjusted by an administrator. Current balance: ${balance / 100:.2f}",
    )


@router.callback_query(F.data == "adm:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminBroadcast.content)
    await callback.message.answer("请输入要广播的消息；发送 /cancel 取消：")


@router.message(AdminBroadcast.content)
async def admin_broadcast_send(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    content = (message.text or "").strip()
    if not 1 <= len(content) <= 4000:
        await message.answer("广播内容长度应为 1–4000 字符。")
        return
    async with SessionFactory() as session:
        recipients = await list_broadcast_user_ids(session)
    await state.update_data(broadcast_content=content)
    await state.set_state(AdminBroadcast.confirmation)
    await message.answer(
        f"📣 即将向 {len(recipients)} 个用户广播：\n\n{html.escape(content)}\n\n确认发送？",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ 确认发送", callback_data="adm:broadcast:confirm", style="success"
                    ),
                    InlineKeyboardButton(
                        text="❌ 取消", callback_data="adm:broadcast:cancel", style="danger"
                    ),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:broadcast:cancel")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.answer("已取消", show_alert=True)


@router.callback_query(F.data == "adm:broadcast:confirm")
async def admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    data = await state.get_data()
    content = data.get("broadcast_content", "")
    if not content:
        await callback.answer("广播内容已失效", show_alert=True)
        return
    async with SessionFactory() as session:
        recipients = await list_broadcast_user_ids(session)
        async with session.begin():
            await record_admin_action(
                session,
                admin_telegram_id=callback.from_user.id,
                action="user_broadcast",
                target_type="users",
                details={"recipient_count": len(recipients)},
            )
    await state.clear()
    await callback.answer("开始发送")
    sent = failed = 0
    for telegram_id in recipients:
        try:
            await callback.bot.send_message(telegram_id, html.escape(content))
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await callback.bot.send_message(telegram_id, html.escape(content))
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await callback.message.answer(f"📣 广播完成\n成功：{sent}\n失败：{failed}")
