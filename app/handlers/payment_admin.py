import html
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
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
    reconcile_linked_refund,
)

router = Router(name="payment-admin")
settings = get_settings()
ACTION_LABELS = {"r": "补发已有卡", "n": "分配新卡", "f": "Stars 退款"}


class ReconcileLinkedRefund(StatesGroup):
    evidence = State()


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
    lines = ["⏳ <b>退款处理中与待对账</b>"]
    rows = []
    for item in pending:
        error = html.escape(item.last_error or "暂无错误")
        lines.append(
            f"\n<code>{item.public_id}</code> · 用户 <code>{item.telegram_user_id}</code>"
            f" · ⭐ {item.total_amount} · {item.payment_status}"
            f"\n尝试 {item.attempts} 次 · {error}"
        )
        if item.payment_status == "refund_unknown":
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ 确认已退款",
                        callback_data=f"adm:linked:reconcile:y:{item.order_id}",
                    ),
                    InlineKeyboardButton(
                        text="🚫 确认未退款",
                        callback_data=f"adm:linked:reconcile:n:{item.order_id}",
                    ),
                ]
            )
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    await callback.message.answer("\n".join(lines), reply_markup=keyboard)


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
    request = None
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
        if request is not None and request.claim_token is not None:
            async with SessionFactory() as session:
                await finish_star_refund(
                    session,
                    order_id=order_id,
                    admin_telegram_user_id=callback.from_user.id,
                    claim_token=request.claim_token,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
        await callback.message.edit_text(
            "⚠️ 退款 API 返回结果不确定，已隔离并等待人工对账。\n"
            f"错误：<code>{html.escape(type(exc).__name__)}</code>"
        )
        return
    if request.claim_token is None:
        await callback.message.edit_text("❌ 退款租约缺失，请人工检查。")
        return
    async with SessionFactory() as session:
        await finish_star_refund(
            session,
            order_id=order_id,
            admin_telegram_user_id=callback.from_user.id,
            claim_token=request.claim_token,
            success=True,
        )
    await callback.message.edit_text("✅ Telegram Stars 退款成功。")


@router.callback_query(F.data.startswith("adm:linked:reconcile:"))
async def start_linked_reconcile(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, verdict, raw_order_id = callback.data.split(":", 4)
        order_id = uuid.UUID(raw_order_id)
        if verdict not in {"y", "n"}:
            raise ValueError
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await state.set_state(ReconcileLinkedRefund.evidence)
    await state.update_data(order_id=str(order_id), verdict=verdict)
    await callback.answer()
    await callback.message.answer(
        "请输入权威核验依据（交易时间、流水尾号或客服工单）。说明将写入审计记录。"
    )


@router.message(ReconcileLinkedRefund.evidence)
async def finish_linked_reconcile(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    evidence = (message.text or "").strip()
    if len(evidence) < 6:
        await message.answer("核验说明过短，请提供至少 6 个字符。")
        return
    data = await state.get_data()
    try:
        order_id = uuid.UUID(data["order_id"])
        async with SessionFactory() as session:
            await reconcile_linked_refund(
                session,
                order_id=order_id,
                refunded=data["verdict"] == "y",
                admin_telegram_user_id=message.from_user.id,
                evidence_note=evidence,
            )
    except (ValueError, PaymentExceptionError) as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    await state.clear()
    verdict_text = "已退款" if data["verdict"] == "y" else "权威确认未退款"
    await message.answer(f"✅ 订单退款已标记为：{verdict_text}。")
