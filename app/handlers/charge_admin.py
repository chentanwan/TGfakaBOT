import html
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.services.admin_access import is_admin
from app.services.admin_confirmations import consume_confirmation, create_confirmation
from app.services.charge_exceptions import (
    ChargeExceptionError,
    claim_orphan_refund,
    finish_orphan_refund,
    list_orphan_charges,
    resolve_orphan_refund,
)

router = Router(name="charge-admin")
settings = get_settings()


class ReconcileCharge(StatesGroup):
    evidence = State()



def _back_markup(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« 管理后台", callback_data=callback_data)]]
    )


@router.callback_query(F.data == "adm:charges:list")
async def orphan_charge_list(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    await callback.answer()
    async with SessionFactory() as session:
        charges = await list_orphan_charges(session)
    if not charges:
        await callback.message.answer(
            "✅ 当前没有未匹配或冲突支付流水。", reply_markup=_back_markup("adm:home")
        )
        return
    lines = ["🧾 <b>孤立支付流水</b>"]
    rows = []
    for charge in charges:
        payer = str(charge.telegram_user_id) if charge.telegram_user_id else "未知"
        related = charge.related_public_id or "未匹配"
        lines.append(
            f"\n<code>{str(charge.charge_id)[:8]}</code> · {charge.resolution_status}"
            f" / {charge.payment_status}"
            f" · ⭐ {charge.total_amount}\n付款用户：<code>{payer}</code>"
            f" · 候选订单：<code>{related}</code>"
        )
        if charge.payment_status == "refund_unknown":
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ 确认已退款",
                        callback_data=f"adm:charge:reconcile:y:{charge.charge_id}",
                    ),
                    InlineKeyboardButton(
                        text="🚫 确认未退款",
                        callback_data=f"adm:charge:reconcile:n:{charge.charge_id}",
                    ),
                ]
            )
        elif charge.telegram_user_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"↩️ 退款 {str(charge.charge_id)[:8]}",
                        callback_data=f"adm:charge:ask:{charge.charge_id}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="« 管理后台", callback_data="adm:home")])
    await callback.message.answer(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("adm:charge:ask:"))
async def ask_orphan_refund(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
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
                ],
                [InlineKeyboardButton(text="取消", callback_data="adm:charges:list")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("adm:charge:do:"))
async def execute_orphan_refund(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
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


@router.callback_query(F.data.startswith("adm:charge:reconcile:"))
async def start_reconcile(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("无权限", show_alert=True)
        return
    try:
        _, _, _, verdict, raw_charge_id = callback.data.split(":", 4)
        charge_id = uuid.UUID(raw_charge_id)
        if verdict not in {"y", "n"}:
            raise ValueError
    except ValueError:
        await callback.answer("参数错误", show_alert=True)
        return
    await state.set_state(ReconcileCharge.evidence)
    await state.update_data(charge_id=str(charge_id), verdict=verdict)
    await callback.answer()
    await callback.message.answer(
        "请输入权威核验依据（交易记录时间、流水尾号或客服工单）。该说明将写入审计记录。"
    )


@router.message(ReconcileCharge.evidence)
async def finish_reconcile(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    evidence = (message.text or "").strip()
    if len(evidence) < 6:
        await message.answer("核验说明过短，请提供至少 6 个字符。")
        return
    data = await state.get_data()
    try:
        charge_id = uuid.UUID(data["charge_id"])
        async with SessionFactory() as session:
            await resolve_orphan_refund(
                session,
                charge_id=charge_id,
                refunded=data["verdict"] == "y",
                admin_telegram_user_id=message.from_user.id,
                evidence_note=evidence,
            )
    except (ValueError, ChargeExceptionError) as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    await state.clear()
    verdict_text = "已退款" if data["verdict"] == "y" else "权威确认未退款"
    await message.answer(f"✅ 流水已标记为：{verdict_text}。")
