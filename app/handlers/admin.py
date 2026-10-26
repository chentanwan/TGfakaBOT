import html
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.plugins.registry import get_plugin_registry
from app.services.admin import create_product, import_cards, list_admin_products, toggle_product

router = Router(name="admin")
settings = get_settings()


class NewProduct(StatesGroup):
    name = State()
    description = State()
    price = State()


class CardImport(StatesGroup):
    content = State()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def admin_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ 新增商品", callback_data="adm:new")],
        [InlineKeyboardButton(text="📦 商品与库存", callback_data="adm:list")],
        [InlineKeyboardButton(text="⚠️ 支付异常", callback_data="adm:pex:list")],
        [InlineKeyboardButton(text="⏳ 退款处理中", callback_data="adm:refunds:pending")],
        [InlineKeyboardButton(text="🧾 孤立支付流水", callback_data="adm:charges:list")],
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
    await message.answer("请输入 Stars 整数价格，例如 50：")


@router.message(NewProduct.price)
async def new_product_price(message: Message, state: FSMContext) -> None:
    try:
        price = int((message.text or "").strip())
        if price < 1:
            raise ValueError
    except ValueError:
        await message.answer("请输入大于 0 的整数 Stars 价格。")
        return
    data = await state.get_data()
    async with SessionFactory() as session:
        product = await create_product(
            session,
            name=data["name"],
            description=data["description"],
            price_xtr=price,
        )
    await state.clear()
    await message.answer(
        f"✅ 商品已创建：<b>{html.escape(product.name)}</b>\n"
        "下一步请在商品列表中导入卡密。",
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
    lines = ["📦 <b>商品与库存</b>"]
    for product in products:
        icon = "✅" if product.status == "active" else "⏸"
        lines.append(
            f"\n{icon} <b>{html.escape(product.name)}</b> · "
            f"⭐ {product.price_xtr} · 库存 {product.available}"
        )
        product_buttons = [
            InlineKeyboardButton(text="导入卡密", callback_data=f"adm:cards:{product.id}"),
            InlineKeyboardButton(text="启停", callback_data=f"adm:toggle:{product.id}"),
        ]
        rows.append(product_buttons)
        plugin_buttons = [
            button
            for plugin in get_plugin_registry().plugins
            if (button := plugin.admin_product_button(product.id)) is not None
        ]
        if plugin_buttons:
            rows.append(plugin_buttons)
    rows.append([InlineKeyboardButton(text="➕ 新增商品", callback_data="adm:new")])
    await callback.message.answer(
        "".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
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
            inserted, duplicate = await import_cards(
                session,
                product_id=product_id,
                secrets=(message.text or "").splitlines(),
            )
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        f"✅ 导入完成\n成功：{inserted}\n重复/跳过：{duplicate}",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data.startswith("adm:toggle:"))
async def product_toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        async with SessionFactory() as session:
            status = await toggle_product(session, product_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"已切换为 {status.value}", show_alert=True)
