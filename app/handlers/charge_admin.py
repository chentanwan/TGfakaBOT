import html
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.services.admin_confirmations import consume_confirmation, create_confirmation
from app.services.charge_exceptions import (
    ChargeExceptionError,
    claim_orphan_refund,
    finish_orphan_refund,
    list_orphan_charges,
)

router = Router(name="charge-admin")
settings = get_settings()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.callback_query(F.data == "adm:charges:list")
async def orphan_charge_list(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        charges = await list_orphan_charges(session)
    if not charges:
        await callback.message.answer("✅ 当前没有未匹配或冲突支付流水。")
        return
    lines = ["🧾 <b>孤立支付流水</b>"]
    rows = []
    for charge in charges:
        payer = str(charge.telegram_user_id) if charge.telegram_user_id else "未知"
        related = charge.related_public_id or "未匹配"
        lines.append(
            f"\n<code>{str(charge.charge_id)[:8]}</code> · {charge.resolution_status}"
            f" · ⭐ {charge.total_amount}\n付款用户：<code>{payer}</code>"
            f" · 候选订单：<code>{related}</code>"
        )
        if charge.telegram_user_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"↩️ 退款 {str(charge.charge_id)[:8]}",
                        callback_data=f"adm:charge:ask:{charge.charge_id}",
                    )
                ]
            )
    await callback.message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("adm:charge:ask:"))
async def ask_orphan_refund(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        charge_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    redis = Redis.from_url(settings.redis_url)
    try:
        token = await create_confirmation(
            redis, admin_id=callback.from_user.id, action="charge_refund", order_id=charge_id
        )
    finally:
        await redis.aclose()
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>确认孤立支付退款</b>\n\n请先核对付款用户、金额和流水。操作不可撤销。",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="确认退款",
                        callback_data=f"adm:charge:do:{charge_id}:{token}",
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:charge:do:"))
async def execute_orphan_refund(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, raw_charge_id, token = callback.data.split(":", 4)
        charge_id = uuid.UUID(raw_charge_id)
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    redis = Redis.from_url(settings.redis_url)
    try:
        confirmed = await consume_confirmation(
            redis,
            token=token,
            admin_id=callback.from_user.id,
            action="charge_refund",
            order_id=charge_id,
        )
    finally:
        await redis.aclose()
    if not confirmed:
        await callback.answer("确认已过期或已经使用", show_alert=True)
        return
    await callback.answer("正在退款…")
    claim = None
    try:
        async with SessionFactory() as session:
            claim = await claim_orphan_refund(
                session,
                charge_id=charge_id,
                admin_telegram_user_id=callback.from_user.id,
            )
        if claim.already_refunded:
            await callback.message.edit_text("✅ 该流水此前已经退款。")
            return
        await callback.bot.refund_star_payment(
            user_id=claim.telegram_user_id,
            telegram_payment_charge_id=claim.telegram_payment_charge_id,
        )
    except ChargeExceptionError as exc:
        await callback.message.edit_text(f"❌ {html.escape(str(exc))}")
        return
    except Exception as exc:
        if claim is not None:
            async with SessionFactory() as session:
                await finish_orphan_refund(
                    session,
                    charge_id=claim.charge_id,
                    claim_token=claim.claim_token,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
        await callback.message.edit_text("⚠️ 退款结果未知，已隔离等待人工对账。")
        return
    async with SessionFactory() as session:
        await finish_orphan_refund(
            session,
            charge_id=claim.charge_id,
            claim_token=claim.claim_token,
            success=True,
        )
    await callback.message.edit_text("✅ 孤立支付流水已退款。")
