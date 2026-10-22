import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import card_vault
from app.db.base import CardStatus, OrderStatus, PaymentStatus
from app.db.models import Card, Order, OrderItem, PaymentCharge, User


class PaymentExceptionError(Exception):
    pass


@dataclass(slots=True)
class PaymentExceptionSummary:
    order_id: uuid.UUID
    public_id: str
    telegram_user_id: int
    product_name: str
    total_amount: int
    reason: str
    available_stock: int
    charge_status: str


@dataclass(slots=True)
class RefundRequest:
    order_id: uuid.UUID
    telegram_user_id: int
    telegram_payment_charge_id: str
    already_refunded: bool = False


@dataclass(slots=True)
class ManualDelivery:
    order_id: uuid.UUID
    public_id: str
    telegram_user_id: int
    product_name: str
    secret: str
    duplicate: bool = False


async def list_payment_exceptions(
    session: AsyncSession, limit: int = 20
) -> list[PaymentExceptionSummary]:
    available_stock = (
        select(func.count(Card.id))
        .where(Card.sku_id == OrderItem.sku_id, Card.status == CardStatus.AVAILABLE)
        .correlate(OrderItem)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Order, User, OrderItem, PaymentCharge, available_stock.label("stock"))
            .join(User, User.id == Order.user_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(PaymentCharge, PaymentCharge.order_id == Order.id)
            .where(Order.status == OrderStatus.PAYMENT_EXCEPTION)
            .order_by(Order.paid_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        PaymentExceptionSummary(
            order_id=order.id,
            public_id=order.public_id,
            telegram_user_id=user.telegram_user_id,
            product_name=item.product_name_snapshot,
            total_amount=order.total_amount,
            reason=order.payment_exception_reason or "unknown",
            available_stock=int(stock),
            charge_status=charge.status.value,
        )
        for order, user, item, charge, stock in rows
    ]


async def fulfill_payment_exception(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    admin_telegram_user_id: int,
    allocate_new: bool,
) -> ManualDelivery:
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(Order, User, OrderItem)
                .join(User, User.id == Order.user_id)
                .join(OrderItem, OrderItem.order_id == Order.id)
                .where(Order.id == order_id)
                .with_for_update(of=(Order, OrderItem))
            )
        ).one_or_none()
        if row is None:
            raise PaymentExceptionError("异常订单不存在")
        order, user, item = row
        existing_card = await session.scalar(
            select(Card).where(Card.order_item_id == item.id).with_for_update()
        )
        if order.status == OrderStatus.FULFILLED:
            if existing_card is None or existing_card.status != CardStatus.DELIVERED:
                raise PaymentExceptionError("已完成订单的卡密记录异常")
            return ManualDelivery(
                order_id=order.id,
                public_id=order.public_id,
                telegram_user_id=user.telegram_user_id,
                product_name=item.product_name_snapshot,
                secret=card_vault.decrypt(existing_card.secret_ciphertext),
                duplicate=True,
            )
        if order.status != OrderStatus.PAYMENT_EXCEPTION:
            raise PaymentExceptionError(f"当前订单状态不可交付：{order.status.value}")
        if existing_card is not None and existing_card.status == CardStatus.RESERVED:
            card = existing_card
        elif not allocate_new:
            raise PaymentExceptionError("订单没有可补发的预留卡，请选择分配新卡")
        else:
            if existing_card is not None:
                raise PaymentExceptionError("订单已有异常卡密绑定，请人工检查")
            card = await session.scalar(
                select(Card)
                .where(Card.sku_id == item.sku_id, Card.status == CardStatus.AVAILABLE)
                .order_by(Card.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if card is None:
                raise PaymentExceptionError("当前没有可分配库存")
            card.order_item_id = item.id
        try:
            secret = card_vault.decrypt(card.secret_ciphertext)
        except Exception as exc:
            raise PaymentExceptionError("卡密解密失败，订单保持异常状态") from exc
        card.status = CardStatus.DELIVERED
        card.delivered_at = now
        card.reserved_until = None
        order.status = OrderStatus.FULFILLED
        order.fulfilled_at = now
        order.resolved_at = now
        order.resolved_by_telegram_id = admin_telegram_user_id
        order.resolution_action = "allocate_new" if allocate_new else "redeliver_reserved"
        return ManualDelivery(
            order_id=order.id,
            public_id=order.public_id,
            telegram_user_id=user.telegram_user_id,
            product_name=item.product_name_snapshot,
            secret=secret,
        )


async def prepare_star_refund(
    session: AsyncSession, *, order_id: uuid.UUID, admin_telegram_user_id: int
) -> RefundRequest:
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(Order, User, PaymentCharge)
                .join(User, User.id == Order.user_id)
                .join(PaymentCharge, PaymentCharge.order_id == Order.id)
                .where(Order.id == order_id)
                .with_for_update(of=(Order, PaymentCharge))
            )
        ).one_or_none()
        if row is None:
            raise PaymentExceptionError("异常订单或支付流水不存在")
        order, user, charge = row
        if order.status == OrderStatus.REFUNDED or charge.status == PaymentStatus.REFUNDED:
            return RefundRequest(
                order_id=order.id,
                telegram_user_id=user.telegram_user_id,
                telegram_payment_charge_id=charge.telegram_payment_charge_id,
                already_refunded=True,
            )
        if order.status == OrderStatus.FULFILLED:
            raise PaymentExceptionError("订单已交付，禁止自动退款")
        if order.status not in {OrderStatus.PAYMENT_EXCEPTION, OrderStatus.REFUND_PENDING}:
            raise PaymentExceptionError(f"当前订单状态不可退款：{order.status.value}")
        delivered_card = await session.scalar(
            select(Card.id)
            .join(OrderItem, OrderItem.id == Card.order_item_id)
            .where(OrderItem.order_id == order.id, Card.status == CardStatus.DELIVERED)
        )
        if delivered_card:
            raise PaymentExceptionError("卡密已交付，禁止自动退款")
        order.status = OrderStatus.REFUND_PENDING
        charge.status = PaymentStatus.REFUND_PENDING
        charge.refund_requested_at = charge.refund_requested_at or now
        charge.refund_last_attempt_at = now
        charge.refund_attempts += 1
        charge.refund_last_error = None
        charge.refund_requested_by_telegram_id = admin_telegram_user_id
        return RefundRequest(
            order_id=order.id,
            telegram_user_id=user.telegram_user_id,
            telegram_payment_charge_id=charge.telegram_payment_charge_id,
        )


async def finish_star_refund(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    admin_telegram_user_id: int,
    success: bool,
    error: str | None = None,
) -> None:
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(Order, PaymentCharge)
                .join(PaymentCharge, PaymentCharge.order_id == Order.id)
                .where(Order.id == order_id)
                .with_for_update(of=(Order, PaymentCharge))
            )
        ).one_or_none()
        if row is None:
            raise PaymentExceptionError("订单不存在")
        order, charge = row
        if success:
            order.status = OrderStatus.REFUNDED
            order.resolved_at = now
            order.resolved_by_telegram_id = admin_telegram_user_id
            order.resolution_action = "refund"
            charge.status = PaymentStatus.REFUNDED
            charge.refunded_at = now
            charge.refund_last_error = None
        else:
            order.status = OrderStatus.PAYMENT_EXCEPTION
            charge.status = PaymentStatus.REFUND_FAILED
            charge.refund_last_error = (error or "unknown refund error")[:1000]
