import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import card_vault
from app.db.base import CardStatus, OrderStatus, ProductStatus
from app.db.models import (
    SKU,
    Card,
    Order,
    OrderItem,
    PaymentCharge,
    Product,
    User,
)

settings = get_settings()


class ShopError(Exception):
    pass


@dataclass(slots=True)
class CatalogProduct:
    id: uuid.UUID
    name: str
    description: str
    price_xtr: int
    stock: int


@dataclass(slots=True)
class CreatedOrder:
    public_id: str
    invoice_payload: str
    product_name: str
    description: str
    total_amount: int


@dataclass(slots=True)
class DeliveredOrder:
    public_id: str
    product_name: str
    total_amount: int
    secret: str
    duplicate: bool = False
    should_deliver: bool = True


async def ensure_user(
    session: AsyncSession, telegram_user_id: int, username: str | None, display_name: str
) -> User:
    await session.execute(
        insert(User)
        .values(
            telegram_user_id=telegram_user_id,
            username=username,
            display_name=display_name,
        )
        .on_conflict_do_update(
            index_elements=["telegram_user_id"],
            set_={"username": username, "display_name": display_name},
        )
    )
    user = await session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
    )
    if user is None:
        raise ShopError("用户记录创建失败")
    return user


async def list_products(session: AsyncSession) -> list[CatalogProduct]:
    stock_count = func.count(Card.id).filter(Card.status == CardStatus.AVAILABLE)
    rows = (
        await session.execute(
            select(Product, SKU, stock_count.label("stock"))
            .join(SKU, SKU.product_id == Product.id)
            .outerjoin(Card, Card.sku_id == SKU.id)
            .where(
                Product.status == ProductStatus.ACTIVE,
                SKU.is_active.is_(True),
                SKU.is_default.is_(True),
            )
            .group_by(Product.id, SKU.id)
            .order_by(Product.created_at.desc())
        )
    ).all()
    return [
        CatalogProduct(
            id=product.id,
            name=product.name,
            description=product.description,
            price_xtr=sku.price_xtr,
            stock=int(stock),
        )
        for product, sku, stock in rows
    ]


async def get_product(session: AsyncSession, product_id: uuid.UUID) -> CatalogProduct | None:
    return next((p for p in await list_products(session) if p.id == product_id), None)


async def create_order(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None,
    display_name: str,
    product_id: uuid.UUID,
) -> CreatedOrder:
    now = datetime.now(UTC)
    async with session.begin():
        user = await ensure_user(session, telegram_user_id, username, display_name)
        row = (
            await session.execute(
                select(Product, SKU)
                .join(SKU, SKU.product_id == Product.id)
                .where(
                    Product.id == product_id,
                    Product.status == ProductStatus.ACTIVE,
                    SKU.is_default.is_(True),
                    SKU.is_active.is_(True),
                )
                .with_for_update(of=SKU)
            )
        ).one_or_none()
        if row is None:
            raise ShopError("商品不存在或已下架")
        product, sku = row
        card = await session.scalar(
            select(Card)
            .where(Card.sku_id == sku.id, Card.status == CardStatus.AVAILABLE)
            .order_by(Card.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if card is None:
            raise ShopError("商品已售罄")

        expires_at = now + timedelta(minutes=settings.order_reservation_minutes)
        order = Order(
            public_id=f"TG{now:%y%m%d}{secrets.token_hex(3).upper()}",
            user_id=user.id,
            status=OrderStatus.PENDING_PAYMENT,
            currency="XTR",
            total_amount=sku.price_xtr,
            invoice_payload=secrets.token_urlsafe(32),
            reservation_expires_at=expires_at,
        )
        session.add(order)
        await session.flush()
        item = OrderItem(
            order_id=order.id,
            sku_id=sku.id,
            product_name_snapshot=product.name,
            sku_name_snapshot=sku.name,
            unit_price=sku.price_xtr,
            quantity=1,
            line_total=sku.price_xtr,
        )
        session.add(item)
        await session.flush()
        card.status = CardStatus.RESERVED
        card.order_item_id = item.id
        card.reserved_until = expires_at

    return CreatedOrder(
        public_id=order.public_id,
        invoice_payload=order.invoice_payload,
        product_name=product.name,
        description=product.description or product.name,
        total_amount=order.total_amount,
    )


async def cancel_unpaid_order(session: AsyncSession, invoice_payload: str) -> None:
    async with session.begin():
        order = await session.scalar(
            select(Order).where(Order.invoice_payload == invoice_payload).with_for_update()
        )
        if order is None or order.status != OrderStatus.PENDING_PAYMENT:
            return
        item_id = await session.scalar(select(OrderItem.id).where(OrderItem.order_id == order.id))
        if item_id:
            card = await session.scalar(
                select(Card).where(Card.order_item_id == item_id).with_for_update()
            )
            if card and card.status == CardStatus.RESERVED:
                card.status = CardStatus.AVAILABLE
                card.order_item_id = None
                card.reserved_until = None
        order.status = OrderStatus.EXPIRED


async def validate_pre_checkout(
    session: AsyncSession,
    *,
    invoice_payload: str,
    telegram_user_id: int,
    currency: str,
    total_amount: int,
) -> tuple[bool, str]:
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(Order, User)
                .join(User, User.id == Order.user_id)
                .where(Order.invoice_payload == invoice_payload)
                .with_for_update(of=Order)
            )
        ).one_or_none()
        if row is None:
            return False, "订单不存在，请重新下单。"
        order, user = row
        if user.telegram_user_id != telegram_user_id:
            return False, "该账单不属于当前用户。"
        if currency != "XTR" or total_amount != order.total_amount:
            return False, "订单金额校验失败，请重新下单。"
        if order.status != OrderStatus.PENDING_PAYMENT:
            return False, "订单已处理或已失效。"
        if order.reservation_expires_at <= now:
            return False, "订单已过期，请重新下单。"
        item_id = await session.scalar(select(OrderItem.id).where(OrderItem.order_id == order.id))
        card = await session.scalar(select(Card).where(Card.order_item_id == item_id))
        if card is None or card.status != CardStatus.RESERVED:
            return False, "库存状态变化，请重新下单。"
        order.status = OrderStatus.PAYMENT_PROCESSING
        order.precheckout_approved_at = now
        order.reservation_expires_at = now + timedelta(hours=24)
        card.reserved_until = order.reservation_expires_at
        return True, ""


async def settle_payment(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None,
    raw_payload: dict,
) -> DeliveredOrder:
    now = datetime.now(UTC)
    pending_error: str | None = None
    result: DeliveredOrder | None = None
    async with session.begin():
        await session.execute(
            insert(PaymentCharge)
            .values(
                telegram_payment_charge_id=telegram_payment_charge_id,
                provider_payment_charge_id=provider_payment_charge_id,
                telegram_user_id=telegram_user_id,
                invoice_payload=invoice_payload,
                currency=currency,
                total_amount=total_amount,
                status="succeeded",
                resolution_status="unmatched",
                raw_payload=raw_payload,
                received_at=now,
            )
            .on_conflict_do_nothing(index_elements=["telegram_payment_charge_id"])
        )
        charge = await session.scalar(
            select(PaymentCharge)
            .where(PaymentCharge.telegram_payment_charge_id == telegram_payment_charge_id)
            .with_for_update()
        )
        if charge is None:
            raise ShopError("支付流水保存失败，请联系客服")
        if charge.order_id is not None:
            linked_order = await session.scalar(
                select(Order).where(Order.id == charge.order_id).with_for_update()
            )
            if linked_order is None:
                pending_error = "支付已记录，但关联订单缺失，请联系客服"
            elif linked_order.status == OrderStatus.FULFILLED:
                result = await _delivered_order(
                    session, linked_order.id, duplicate=True, include_secret=False
                )
            elif linked_order.status == OrderStatus.PAYMENT_EXCEPTION:
                pending_error = "支付已记录，订单正在人工处理中，请联系客服"
            elif linked_order.status == OrderStatus.REFUND_PENDING:
                pending_error = "该笔支付正在退款处理中"
            elif linked_order.status == OrderStatus.REFUNDED:
                pending_error = "该笔支付已经退款"
            else:
                pending_error = "支付已记录，订单状态正在处理中"
        else:
            row = (
                await session.execute(
                    select(Order, User)
                    .join(User, User.id == Order.user_id)
                    .where(Order.invoice_payload == invoice_payload)
                    .with_for_update(of=Order)
                )
            ).one_or_none()
            if row is None:
                pending_error = "付款已记录，但订单无法匹配，请联系客服"
            else:
                order, user = row
                other_charge = await session.scalar(
                    select(PaymentCharge.id).where(
                        PaymentCharge.order_id == order.id,
                        PaymentCharge.id != charge.id,
                    )
                )
                if other_charge is not None:
                    charge.related_order_id = order.id
                    charge.resolution_status = "conflict"
                    pending_error = "检测到订单重复扣款，已保留流水等待退款处理"
                elif (
                    user.telegram_user_id != telegram_user_id
                    or currency != "XTR"
                    or total_amount != order.total_amount
                ):
                    charge.related_order_id = order.id
                    charge.resolution_status = "conflict"
                    pending_error = "付款已记录，但订单校验异常，请联系客服"
                else:
                    item = await session.scalar(
                        select(OrderItem).where(OrderItem.order_id == order.id)
                    )
                    card = (
                        await session.scalar(
                            select(Card).where(Card.order_item_id == item.id).with_for_update()
                        )
                        if item
                        else None
                    )
                    charge.order_id = order.id
                    charge.resolution_status = "linked"
                    order.paid_at = now
                    if card is None or card.status != CardStatus.RESERVED:
                        order.status = OrderStatus.PAYMENT_EXCEPTION
                        order.payment_exception_reason = "reserved_card_missing_or_invalid"
                        pending_error = "支付成功，订单正在人工处理中，请联系客服"
                    else:
                        try:
                            secret = card_vault.decrypt(card.secret_ciphertext)
                        except Exception:
                            order.status = OrderStatus.PAYMENT_EXCEPTION
                            order.payment_exception_reason = "card_secret_decryption_failed"
                            pending_error = "支付成功，但交付数据异常，请联系客服"
                        else:
                            card.status = CardStatus.DELIVERED
                            card.delivered_at = now
                            card.reserved_until = None
                            order.status = OrderStatus.FULFILLED
                            order.fulfilled_at = now
                            result = DeliveredOrder(
                                public_id=order.public_id,
                                product_name=item.product_name_snapshot,
                                total_amount=order.total_amount,
                                secret=secret,
                            )

    if pending_error:
        raise ShopError(pending_error)
    if result is None:
        raise ShopError("支付已记录，但订单处理结果异常，请联系客服")
    return result


async def _delivered_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    duplicate: bool,
    include_secret: bool = True,
) -> DeliveredOrder:
    row = (
        await session.execute(
            select(Order, OrderItem, Card)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(Card, Card.order_item_id == OrderItem.id)
            .where(Order.id == order_id)
        )
    ).one()
    order, item, card = row
    return DeliveredOrder(
        public_id=order.public_id,
        product_name=item.product_name_snapshot,
        total_amount=order.total_amount,
        secret=card_vault.decrypt(card.secret_ciphertext) if include_secret else "",
        duplicate=duplicate,
        should_deliver=include_secret,
    )


async def get_user_order(
    session: AsyncSession, telegram_user_id: int, public_id: str
) -> DeliveredOrder | None:
    row = (
        await session.execute(
            select(Order, OrderItem, Card)
            .join(User, User.id == Order.user_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .outerjoin(Card, Card.order_item_id == OrderItem.id)
            .where(
                User.telegram_user_id == telegram_user_id,
                Order.public_id == public_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    order, item, card = row
    return DeliveredOrder(
        public_id=order.public_id,
        product_name=item.product_name_snapshot,
        total_amount=order.total_amount,
        secret=(
            card_vault.decrypt(card.secret_ciphertext)
            if card and order.status == OrderStatus.FULFILLED
            else ""
        ),
    )


async def list_user_orders(
    session: AsyncSession, telegram_user_id: int, *, offset: int = 0, limit: int = 5
) -> list[DeliveredOrder]:
    rows = (
        await session.execute(
            select(Order, OrderItem, Card)
            .join(User, User.id == Order.user_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .outerjoin(Card, Card.order_item_id == OrderItem.id)
            .where(User.telegram_user_id == telegram_user_id)
            .order_by(Order.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [
        DeliveredOrder(
            public_id=order.public_id,
            product_name=item.product_name_snapshot,
            total_amount=order.total_amount,
            secret="",
        )
        for order, item, card in rows
    ]
