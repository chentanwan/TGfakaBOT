import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import card_vault
from app.db.base import CardStatus, OrderStatus
from app.db.models import Card, Order, OrderItem, PluginPayment, User
from app.plugins.registry import get_plugin_registry
from app.services.audit import record_admin_action
from app.services.supplier_routing import reserve_supplier_route


class PluginPaymentError(Exception):
    pass


@dataclass(slots=True)
class PluginDelivery:
    telegram_user_id: int
    public_id: str
    product_name: str
    secret: str
    should_deliver: bool
    exception_message: str | None = None
    needs_supplier: bool = False
    order_id: uuid.UUID | None = None


async def reserve_plugin_payment(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    provider_data: dict | None = None,
) -> PluginPayment:
    async with session.begin():
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None or order.payment_provider != provider:
            raise PluginPaymentError("订单与支付插件不匹配")
        if order.status != OrderStatus.PENDING_PAYMENT:
            raise PluginPaymentError("订单已处理或失效")
        payment = await session.scalar(
            select(PluginPayment).where(PluginPayment.order_id == order.id).with_for_update()
        )
        if payment is None:
            payment = PluginPayment(
                order_id=order.id,
                provider=provider,
                status="creating",
                currency=order.currency,
                amount_minor=order.total_amount,
                provider_data=provider_data or {},
            )
            session.add(payment)
            await session.flush()
        elif provider_data:
            payment.provider_data = {**payment.provider_data, **provider_data}
        return payment


async def save_plugin_checkout(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    external_id: str,
    checkout_url: str,
    expires_at,
    provider_data: dict,
) -> None:
    async with session.begin():
        payment = await session.scalar(
            select(PluginPayment)
            .where(PluginPayment.order_id == order_id, PluginPayment.provider == provider)
            .with_for_update()
        )
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if payment is None or order is None:
            raise PluginPaymentError("插件支付记录不存在")
        payment.external_id = external_id
        payment.checkout_url = checkout_url
        payment.expires_at = expires_at
        payment.provider_data = provider_data
        payment.status = "waiting"
        payment.last_error = None
        order.reservation_expires_at = expires_at
        item_id = await session.scalar(select(OrderItem.id).where(OrderItem.order_id == order.id))
        card = await session.scalar(
            select(Card).where(Card.order_item_id == item_id).with_for_update()
        )
        if card and card.status == CardStatus.RESERVED:
            card.reserved_until = expires_at


async def record_plugin_payment_error(
    session: AsyncSession, *, order_id: uuid.UUID, provider: str, error: str
) -> None:
    async with session.begin():
        payment = await session.scalar(
            select(PluginPayment)
            .where(PluginPayment.order_id == order_id, PluginPayment.provider == provider)
            .with_for_update()
        )
        if payment:
            payment.status = "creation_unknown"
            payment.last_error = error[:2000]


async def fulfill_plugin_payment(
    session: AsyncSession,
    *,
    provider: str,
    merchant_order_id: str,
    transaction_id: str,
    amount_minor: int,
    event: dict,
    provider_data: dict | None = None,
) -> PluginDelivery:
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(PluginPayment, Order, User)
                .join(Order, Order.id == PluginPayment.order_id)
                .join(User, User.id == Order.user_id)
                .where(
                    PluginPayment.provider == provider,
                    Order.public_id == merchant_order_id,
                )
                .with_for_update(of=(PluginPayment, Order))
            )
        ).one_or_none()
        if row is None:
            raise PluginPaymentError("未知插件支付订单")
        payment, order, user = row
        payment.last_event = event
        if payment.amount_minor != amount_minor or order.total_amount != amount_minor:
            payment.status = "conflict"
            payment.last_error = "callback amount mismatch"
            order.status = OrderStatus.PAYMENT_EXCEPTION
            order.payment_exception_reason = f"{provider}_amount_mismatch"
            return PluginDelivery(
                user.telegram_user_id,
                order.public_id,
                "",
                "",
                False,
                "到账金额校验异常，已转人工处理。",
            )
        if payment.status == "paid" and order.status == OrderStatus.FULFILLED:
            return PluginDelivery(user.telegram_user_id, order.public_id, "", "", False)
        existing_transaction = await session.scalar(
            select(PluginPayment.id).where(
                PluginPayment.transaction_id == transaction_id,
                PluginPayment.id != payment.id,
            )
        )
        if existing_transaction:
            raise PluginPaymentError("交易 ID 已被其他订单使用")
        payment.transaction_id = transaction_id
        payment.status = "paid"
        payment.paid_at = payment.paid_at or now
        if provider_data:
            payment.provider_data = {**payment.provider_data, **provider_data}
        order.paid_at = order.paid_at or now
        item = await session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
        card = (
            await session.scalar(
                select(Card).where(Card.order_item_id == item.id).with_for_update()
            )
            if item
            else None
        )
        supplier_route = (
            await reserve_supplier_route(
                session,
                order=order,
                sku_id=item.sku_id,
                available_providers=get_plugin_registry().supplier_providers,
            )
            if item and card is None
            else None
        )
        if card is None and supplier_route is not None:
            order.status = OrderStatus.PAID
            return PluginDelivery(
                user.telegram_user_id,
                order.public_id,
                item.product_name_snapshot,
                "",
                False,
                needs_supplier=True,
                order_id=order.id,
            )
        if card is None or card.status != CardStatus.RESERVED:
            order.status = OrderStatus.PAYMENT_EXCEPTION
            order.payment_exception_reason = f"{provider}_reserved_card_missing_or_invalid"
            return PluginDelivery(
                user.telegram_user_id,
                order.public_id,
                item.product_name_snapshot if item else "",
                "",
                False,
                "款项已到账，但库存状态异常，已转人工处理。",
            )
        secret = card_vault.decrypt(card.secret_ciphertext)
        card.status = CardStatus.DELIVERED
        card.delivered_at = now
        card.reserved_until = None
        order.status = OrderStatus.FULFILLED
        order.fulfilled_at = now
        return PluginDelivery(
            user.telegram_user_id,
            order.public_id,
            item.product_name_snapshot,
            secret,
            True,
        )


async def update_plugin_payment_status(
    session: AsyncSession,
    *,
    provider: str,
    merchant_order_id: str,
    status: str,
    event: dict,
) -> None:
    async with session.begin():
        row = (
            await session.execute(
                select(PluginPayment, Order)
                .join(Order, Order.id == PluginPayment.order_id)
                .where(
                    PluginPayment.provider == provider,
                    Order.public_id == merchant_order_id,
                )
                .with_for_update(of=(PluginPayment, Order))
            )
        ).one_or_none()
        if row is None:
            raise PluginPaymentError("未知插件支付订单")
        payment, _ = row
        payment.last_event = event
        if payment.status != "paid":
            payment.status = status


async def submit_manual_usdt_proof(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    proof: str,
) -> None:
    if not provider.startswith("usdt_"):
        raise PluginPaymentError("不支持的 USDT 支付方式")
    proof = proof.strip()
    if provider in {"usdt_bep20", "usdt_arbitrum"}:
        proof = proof.lower()
    if len(proof) < 4 or len(proof) > 255:
        raise PluginPaymentError("付款凭证格式无效")
    try:
        async with session.begin():
            row = (
                await session.execute(
                    select(PluginPayment, Order)
                    .join(Order, Order.id == PluginPayment.order_id)
                    .where(
                        PluginPayment.order_id == order_id,
                        PluginPayment.provider == provider,
                    )
                    .with_for_update(of=(PluginPayment, Order))
                )
            ).one_or_none()
            if row is None:
                raise PluginPaymentError("USDT 支付订单不存在")
            payment, order = row
            if order.status != OrderStatus.PENDING_PAYMENT:
                raise PluginPaymentError("订单已处理或失效")
            duplicate = await session.scalar(
                select(PluginPayment.id).where(
                    PluginPayment.provider == provider,
                    PluginPayment.transaction_id == proof,
                    PluginPayment.id != payment.id,
                )
            )
            if duplicate is not None:
                raise PluginPaymentError("该付款凭证已提交")
            payment.transaction_id = proof
            payment.status = "proof_pending"
            payment.provider_data = {**payment.provider_data, "proof": proof}
    except IntegrityError as exc:
        raise PluginPaymentError("该付款凭证已提交") from exc


@dataclass(slots=True)
class ManualUsdtReview:
    payment_id: uuid.UUID
    order_id: uuid.UUID
    public_id: str
    telegram_user_id: int
    product_name: str
    provider: str
    amount_minor: int
    proof: str
    destination: str
    method_label: str
    created_at: object
    overdue: bool


async def list_manual_usdt_reviews(
    session: AsyncSession, *, limit: int = 30
) -> list[ManualUsdtReview]:
    rows = (
        await session.execute(
            select(PluginPayment, Order, OrderItem, User)
            .join(Order, Order.id == PluginPayment.order_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(User, User.id == Order.user_id)
            .where(
                PluginPayment.provider.startswith("usdt_"),
                PluginPayment.status == "proof_pending",
                Order.status == OrderStatus.PENDING_PAYMENT,
            )
            .order_by(PluginPayment.created_at.asc())
            .limit(limit)
        )
    ).all()
    return [
        ManualUsdtReview(
            payment.id, order.id, order.public_id, user.telegram_user_id,
            item.product_name_snapshot, payment.provider, payment.amount_minor,
            payment.transaction_id or "",
            str(payment.provider_data.get("destination") or ""),
            str(payment.provider_data.get("method_label") or payment.provider),
            payment.created_at,
            (datetime.now(UTC) - payment.created_at).total_seconds() > 86400,
        )
        for payment, order, item, user in rows
    ]


async def approve_manual_usdt_payment(
    session: AsyncSession,
    *,
    payment_id: uuid.UUID,
    admin_telegram_id: int,
) -> tuple[PluginDelivery, bool]:
    payment = await session.scalar(select(PluginPayment).where(PluginPayment.id == payment_id))
    if payment is None or not payment.provider.startswith("usdt_"):
        raise PluginPaymentError("USDT 支付记录不存在")
    if payment.status == "paid":
        order = await session.get(Order, payment.order_id)
        if order is None:
            raise PluginPaymentError("订单不存在")
        user = await session.get(User, order.user_id)
        if user is None:
            raise PluginPaymentError("用户不存在")
        delivery = PluginDelivery(user.telegram_user_id, order.public_id, "", "", False)
        await session.rollback()
        return delivery, True
    if payment.status != "proof_pending" or not payment.transaction_id:
        raise PluginPaymentError("付款凭证尚未处于待审核状态")
    order = await session.get(Order, payment.order_id)
    if order is None:
        raise PluginPaymentError("订单不存在")
    provider = payment.provider
    transaction_id = payment.transaction_id
    amount_minor = payment.amount_minor
    public_id = order.public_id
    await session.rollback()
    delivery = await fulfill_plugin_payment(
        session,
        provider=provider,
        merchant_order_id=public_id,
        transaction_id=transaction_id,
        amount_minor=amount_minor,
        event={"type": "manual_admin_approval", "admin_id": admin_telegram_id},
        provider_data={"reviewed_by_telegram_id": admin_telegram_id},
    )
    async with session.begin():
        await record_admin_action(
            session, admin_telegram_id=admin_telegram_id,
            action="manual_usdt_payment_review", target_type="plugin_payment",
            target_id=str(payment_id), details={"approved": True},
        )
    return delivery, False


async def reject_manual_usdt_payment(
    session: AsyncSession,
    *,
    payment_id: uuid.UUID,
    admin_telegram_id: int,
) -> tuple[int, str, bool]:
    async with session.begin():
        row = (
            await session.execute(
                select(PluginPayment, Order, User)
                .join(Order, Order.id == PluginPayment.order_id)
                .join(User, User.id == Order.user_id)
                .where(PluginPayment.id == payment_id)
                .with_for_update(of=(PluginPayment, Order))
            )
        ).one_or_none()
        if row is None:
            raise PluginPaymentError("USDT 支付记录不存在")
        payment, order, user = row
        if payment.status == "rejected":
            return user.telegram_user_id, order.public_id, True
        if payment.status != "proof_pending" or order.status != OrderStatus.PENDING_PAYMENT:
            raise PluginPaymentError("付款记录已处理或状态异常")
        item = await session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
        card = (
            await session.scalar(
                select(Card).where(Card.order_item_id == item.id).with_for_update()
            )
            if item else None
        )
        if card and card.status == CardStatus.RESERVED:
            card.status = CardStatus.AVAILABLE
            card.order_item_id = None
            card.reserved_until = None
        payment.status = "rejected"
        payment.last_error = "rejected by administrator"
        order.status = OrderStatus.EXPIRED
        await record_admin_action(
            session, admin_telegram_id=admin_telegram_id,
            action="manual_usdt_payment_review", target_type="plugin_payment",
            target_id=str(payment.id), details={"approved": False},
        )
        return user.telegram_user_id, order.public_id, False
