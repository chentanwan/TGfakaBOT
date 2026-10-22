import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import PaymentStatus
from app.db.models import Order, PaymentCharge

CLAIM_TTL = timedelta(minutes=10)


class ChargeExceptionError(Exception):
    pass


@dataclass(slots=True)
class OrphanChargeSummary:
    charge_id: uuid.UUID
    telegram_user_id: int | None
    telegram_payment_charge_id: str
    invoice_payload: str
    total_amount: int
    resolution_status: str
    related_public_id: str | None
    received_at: datetime


@dataclass(slots=True)
class ChargeRefundClaim:
    charge_id: uuid.UUID
    telegram_user_id: int
    telegram_payment_charge_id: str
    claim_token: uuid.UUID
    already_refunded: bool = False


async def list_orphan_charges(
    session: AsyncSession, limit: int = 20
) -> list[OrphanChargeSummary]:
    rows = (
        await session.execute(
            select(PaymentCharge, Order)
            .outerjoin(Order, Order.id == PaymentCharge.related_order_id)
            .where(
                PaymentCharge.order_id.is_(None),
                PaymentCharge.resolution_status.in_(["unmatched", "conflict"]),
                PaymentCharge.status != PaymentStatus.REFUNDED,
            )
            .order_by(PaymentCharge.received_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        OrphanChargeSummary(
            charge_id=charge.id,
            telegram_user_id=charge.telegram_user_id,
            telegram_payment_charge_id=charge.telegram_payment_charge_id,
            invoice_payload=charge.invoice_payload,
            total_amount=charge.total_amount,
            resolution_status=charge.resolution_status,
            related_public_id=order.public_id if order else None,
            received_at=charge.received_at,
        )
        for charge, order in rows
    ]


async def claim_orphan_refund(
    session: AsyncSession, *, charge_id: uuid.UUID, admin_telegram_user_id: int
) -> ChargeRefundClaim:
    now = datetime.now(UTC)
    async with session.begin():
        charge = await session.scalar(
            select(PaymentCharge).where(PaymentCharge.id == charge_id).with_for_update()
        )
        if charge is None or charge.order_id is not None:
            raise ChargeExceptionError("孤立支付流水不存在或已经关联订单")
        if charge.status == PaymentStatus.REFUNDED:
            if charge.telegram_user_id is None:
                raise ChargeExceptionError("已退款流水缺少付款用户记录")
            return ChargeRefundClaim(
                charge_id=charge.id,
                telegram_user_id=charge.telegram_user_id,
                telegram_payment_charge_id=charge.telegram_payment_charge_id,
                claim_token=charge.refund_claim_token or uuid.uuid4(),
                already_refunded=True,
            )
        if charge.telegram_user_id is None:
            raise ChargeExceptionError("流水缺少付款用户 ID，禁止自动退款")
        if charge.status == PaymentStatus.REFUND_UNKNOWN:
            raise ChargeExceptionError("退款结果未知，必须人工对账，禁止自动重试")
        if charge.status == PaymentStatus.REFUND_PENDING:
            if charge.refund_lease_expires_at and charge.refund_lease_expires_at <= now:
                charge.status = PaymentStatus.REFUND_UNKNOWN
                charge.refund_claim_token = None
                charge.refund_claimed_at = None
                charge.refund_lease_expires_at = None
                charge.refund_last_error = "refund lease expired; result unknown"
                raise ChargeExceptionError("上次退款结果未知，已转入人工对账")
            raise ChargeExceptionError("该流水正在由其他管理员退款")
        claim_token = uuid.uuid4()
        charge.status = PaymentStatus.REFUND_PENDING
        charge.refund_requested_at = charge.refund_requested_at or now
        charge.refund_last_attempt_at = now
        charge.refund_attempts += 1
        charge.refund_requested_by_telegram_id = admin_telegram_user_id
        charge.refund_claim_token = claim_token
        charge.refund_claimed_at = now
        charge.refund_lease_expires_at = now + CLAIM_TTL
        charge.refund_last_error = None
        return ChargeRefundClaim(
            charge_id=charge.id,
            telegram_user_id=charge.telegram_user_id,
            telegram_payment_charge_id=charge.telegram_payment_charge_id,
            claim_token=claim_token,
        )


async def finish_orphan_refund(
    session: AsyncSession,
    *,
    charge_id: uuid.UUID,
    claim_token: uuid.UUID,
    success: bool,
    error: str | None = None,
) -> None:
    async with session.begin():
        charge = await session.scalar(
            select(PaymentCharge).where(PaymentCharge.id == charge_id).with_for_update()
        )
        if charge is None or charge.refund_claim_token != claim_token:
            raise ChargeExceptionError("退款租约已失效，拒绝写入结果")
        if success:
            charge.status = PaymentStatus.REFUNDED
            charge.refunded_at = datetime.now(UTC)
            charge.resolution_status = "refunded"
            charge.refund_last_error = None
        else:
            charge.status = PaymentStatus.REFUND_UNKNOWN
            charge.refund_last_error = (error or "unknown refund result")[:1000]
        charge.refund_claim_token = None
        charge.refund_claimed_at = None
        charge.refund_lease_expires_at = None


async def resolve_orphan_refund(
    session: AsyncSession, *, charge_id: uuid.UUID, refunded: bool
) -> None:
    async with session.begin():
        charge = await session.scalar(
            select(PaymentCharge).where(PaymentCharge.id == charge_id).with_for_update()
        )
        if charge is None or charge.order_id is not None:
            raise ChargeExceptionError("孤立支付流水不存在")
        if charge.refund_lease_expires_at and charge.refund_lease_expires_at > datetime.now(UTC):
            raise ChargeExceptionError("退款请求仍可能在途，暂不可人工处理")
        if refunded:
            charge.status = PaymentStatus.REFUNDED
            charge.refunded_at = charge.refunded_at or datetime.now(UTC)
            charge.resolution_status = "refunded"
            charge.refund_last_error = None
        else:
            charge.status = PaymentStatus.REFUND_FAILED
            charge.resolution_status = "conflict"
            charge.refund_last_error = "authority confirmed refund was not executed"
        charge.refund_claim_token = None
        charge.refund_claimed_at = None
        charge.refund_lease_expires_at = None
