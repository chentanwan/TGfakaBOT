import html
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.services.payment_exceptions import (
    PaymentExceptionError,
    finish_star_refund,
    list_payment_exceptions,
    prepare_star_refund,
)

router = Router(name="payment-admin")
settings = get_settings()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.callback_query(F.data == "adm:pex:list")
async def payment_exception_list(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        exceptions = await list_payment_exceptions(session)
    if not exceptions:
        await callback.message.answer("✅ 当前没有待处理支付异常。")
        return
    lines = ["⚠️ <b>支付异常订单</b>"]
    rows = []
    for item in exceptions:
        lines.append(
            f"\n<code>{item.public_id}</code> · {html.escape(item.product_name)}\n"
            f"用户：<code>{item.telegram_user_id}</code> · ⭐ {item.total_amount}\n"
            f"原因：<code>{html.escape(item.reason)}</code> · 可用库存 {item.available_stock}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"↩️ 退款 {item.public_id}",
                    callback_data=f"adm:pex:ask:{item.order_id}",
                )
            ]
        )
    await callback.message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("adm:pex:ask:"))
async def ask_refund(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        order_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>确认 Stars 退款</b>\n\n"
        "退款成功后不可撤销；已曝光卡密不会重新进入库存。",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="确认退款",
                        callback_data=f"adm:pex:refund:{order_id}",
                    ),
                    InlineKeyboardButton(text="取消", callback_data="adm:pex:cancel"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:pex:cancel")
async def cancel_refund(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer("已取消")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("adm:pex:refund:"))
async def confirm_refund(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        order_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await callback.answer("正在退款，请勿重复操作…")
    try:
        async with SessionFactory() as session:
            request = await prepare_star_refund(
                session,
                order_id=order_id,
                admin_telegram_user_id=callback.from_user.id,
            )
        if request.already_refunded:
            await callback.message.edit_text("✅ 该订单此前已经退款。")
            return
        await callback.bot.refund_star_payment(
            user_id=request.telegram_user_id,
            telegram_payment_charge_id=request.telegram_payment_charge_id,
        )
    except PaymentExceptionError as exc:
        await callback.message.edit_text(f"❌ {html.escape(str(exc))}")
        return
    except Exception as exc:
        # A timeout may have an unknown result; keep REFUND_PENDING for reconciliation.
        await callback.message.edit_text(
            "⚠️ 退款 API 返回结果不确定，订单保持退款处理中。\n"
            f"错误：<code>{html.escape(type(exc).__name__)}</code>"
        )
        return
    async with SessionFactory() as session:
        await finish_star_refund(
            session,
            order_id=order_id,
            admin_telegram_user_id=callback.from_user.id,
            success=True,
        )
    await callback.message.edit_text("✅ Telegram Stars 退款成功。")
