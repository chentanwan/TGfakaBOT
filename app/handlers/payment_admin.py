import html
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.services.admin_confirmations import consume_confirmation, create_confirmation
from app.services.payment_exceptions import (
    PaymentExceptionError,
    finish_star_refund,
    fulfill_payment_exception,
    list_payment_exceptions,
    list_refund_pending,
    prepare_star_refund,
    record_refund_uncertain,
)

router = Router(name="payment-admin")
settings = get_settings()
ACTION_LABELS = {"r": "补发已有卡", "n": "分配新卡", "f": "Stars 退款"}


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
                    text="📨 补发", callback_data=f"adm:pex:ask:r:{item.order_id}"
                ),
                InlineKeyboardButton(
                    text="🆕 新卡", callback_data=f"adm:pex:ask:n:{item.order_id}"
                ),
                InlineKeyboardButton(
                    text="↩️ 退款", callback_data=f"adm:pex:ask:f:{item.order_id}"
                ),
            ]
        )
    await callback.message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data == "adm:refunds:pending")
async def refund_pending_list(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        pending = await list_refund_pending(session)
    if not pending:
        await callback.message.answer("✅ 当前没有退款处理中的订单。")
        return
    lines = ["⏳ <b>退款处理中</b>"]
    for item in pending:
        error = html.escape(item.last_error or "暂无错误")
        lines.append(
            f"\n<code>{item.public_id}</code> · 用户 <code>{item.telegram_user_id}</code>"
            f" · ⭐ {item.total_amount}\n尝试 {item.attempts} 次 · {error}"
        )
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("adm:pex:ask:"))
async def ask_action(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, action, raw_order_id = callback.data.split(":", 4)
        order_id = uuid.UUID(raw_order_id)
        label = ACTION_LABELS[action]
    except (ValueError, KeyError):
        await callback.answer("参数错误", show_alert=True)
        return
    redis = Redis.from_url(settings.redis_url)
    try:
        token = await create_confirmation(
            redis, admin_id=callback.from_user.id, action=action, order_id=order_id
        )
    finally:
        await redis.aclose()
    await callback.answer()
    await callback.message.answer(
        f"⚠️ <b>确认{label}</b>\n\n确认令牌 5 分钟内有效且只能使用一次。",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"确认{label}",
                        callback_data=f"adm:pex:do:{action}:{order_id}:{token}",
                    ),
                    InlineKeyboardButton(text="取消", callback_data="adm:pex:cancel"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "adm:pex:cancel")
async def cancel_action(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer("已取消")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("adm:pex:do:"))
async def confirm_action(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, action, raw_order_id, token = callback.data.split(":", 5)
        order_id = uuid.UUID(raw_order_id)
        ACTION_LABELS[action]
    except (ValueError, KeyError):
        await callback.answer("参数错误", show_alert=True)
        return
    redis = Redis.from_url(settings.redis_url)
    try:
        confirmed = await consume_confirmation(
            redis,
            token=token,
            admin_id=callback.from_user.id,
            action=action,
            order_id=order_id,
        )
    finally:
        await redis.aclose()
    if not confirmed:
        await callback.answer("确认已过期或已经使用", show_alert=True)
        return
    await callback.answer("正在处理，请勿重复操作…")
    if action == "f":
        await _execute_refund(callback, order_id)
    else:
        await _execute_delivery(callback, order_id, allocate_new=action == "n")


async def _execute_delivery(
    callback: CallbackQuery, order_id: uuid.UUID, *, allocate_new: bool
) -> None:
    try:
        async with SessionFactory() as session:
            delivery = await fulfill_payment_exception(
                session,
                order_id=order_id,
                admin_telegram_user_id=callback.from_user.id,
                allocate_new=allocate_new,
            )
        await callback.bot.send_message(
            delivery.telegram_user_id,
            "✅ <b>订单已由管理员处理完成</b>\n\n"
            f"商品：{html.escape(delivery.product_name)}\n"
            f"订单：<code>{delivery.public_id}</code>\n"
            f"卡密：<code>{html.escape(delivery.secret)}</code>\n\n"
            "请妥善保管卡密，不要转发或公开。",
        )
    except PaymentExceptionError as exc:
        await callback.message.edit_text(f"❌ {html.escape(str(exc))}")
        return
    except Exception as exc:
        await callback.message.edit_text(
            "⚠️ 订单状态已处理，但向用户发送消息失败。用户仍可通过 /orders 找回。\n"
            f"错误：<code>{html.escape(type(exc).__name__)}</code>"
        )
        return
    note = "（订单此前已完成，本次仅补发消息）" if delivery.duplicate else ""
    await callback.message.edit_text(f"✅ 交付完成。{note}")


async def _execute_refund(callback: CallbackQuery, order_id: uuid.UUID) -> None:
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
        error = f"{type(exc).__name__}: {exc}"
        async with SessionFactory() as session:
            await record_refund_uncertain(session, order_id=order_id, error=error)
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
