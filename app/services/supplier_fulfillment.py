import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import CardStatus, OrderStatus
from app.db.models import Card, Order, OrderItem, SupplierProduct, User


class SupplierFulfillmentError(Exception):
    pass


@dataclass(slots=True)
class SupplierPurchaseContext:
    order_id: uuid.UUID
    public_id: str
    telegram_user_id: int
    product_name: str
    external_product_id: str
    expected_cost_minor: int
    currency: str
    sale_currency: str
    sale_amount_minor: int


@dataclass(slots=True)
class SupplierDelivery:
    telegram_user_id: int
    public_id: str
    product_name: str
    secret: str
    should_deliver: bool


async def get_supplier_purchase_context(
    session: AsyncSession, *, order_id: uuid.UUID, provider: str
) -> SupplierPurchaseContext | None:
    row = (
        await session.execute(
            select(Order, OrderItem, User, SupplierProduct)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(User, User.id == Order.user_id)
            .join(
                SupplierProduct,
                (SupplierProduct.sku_id == OrderItem.sku_id)
                & (SupplierProduct.provider == provider),
            )
            .where(Order.id == order_id)
        )
    ).one_or_none()
    if row is None:
        return None
    order, item, user, supplier = row
    return SupplierPurchaseContext(
        order_id=order.id,
        public_id=order.public_id,
        telegram_user_id=user.telegram_user_id,
        product_name=item.product_name_snapshot,
        external_product_id=supplier.external_product_id,
        expected_cost_minor=supplier.cost_amount_minor,
        currency=supplier.cost_currency,
        sale_currency=order.currency,
        sale_amount_minor=order.total_amount,
    )


async def fulfill_supplier_order(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    external_order_id: str,
    delivered_secret: str,
    supplier_amount_minor: int,
    supplier_response: dict,
    sale_currency: str,
    sale_amount_minor: int,
) -> SupplierDelivery:
    if not delivered_secret:
        raise SupplierFulfillmentError("上游订单尚未交付卡密")
    now = datetime.now(UTC)
    async with session.begin():
        row = (
            await session.execute(
                select(Order, OrderItem, User, SupplierProduct)
                .join(OrderItem, OrderItem.order_id == Order.id)
                .join(User, User.id == Order.user_id)
                .join(
                    SupplierProduct,
                    (SupplierProduct.sku_id == OrderItem.sku_id)
                    & (SupplierProduct.provider == provider),
                )
                .where(Order.id == order_id)
                .with_for_update(of=Order)
            )
        ).one_or_none()
        if row is None:
            raise SupplierFulfillmentError("订单未配置该上游供货商")
        order, item, user, supplier = row
        if order.currency != sale_currency or order.total_amount != sale_amount_minor:
            order.status = OrderStatus.PAYMENT_EXCEPTION
            order.payment_exception_reason = f"{provider}_sale_snapshot_mismatch"
            raise SupplierFulfillmentError("销售订单金额快照不一致")
        if supplier.cost_currency != order.currency:
            order.status = OrderStatus.PAYMENT_EXCEPTION
            order.payment_exception_reason = f"{provider}_currency_mismatch"
            raise SupplierFulfillmentError("上游采购成本与销售订单币种不一致")
        if supplier_amount_minor > order.total_amount:
            order.status = OrderStatus.PAYMENT_EXCEPTION
            order.payment_exception_reason = f"{provider}_cost_exceeds_sale_price"
            raise SupplierFulfillmentError("上游采购成本高于本订单销售金额")
        card = await session.scalar(
            select(Card).where(Card.order_item_id == item.id).with_for_update()
        )
        if card is not None:
            raise SupplierFulfillmentError("订单已有本地库存卡密，拒绝重复上游交付")
        # Supplier secrets are encrypted by the same vault and become regular delivered inventory.
        from app.core.security import card_vault

        card = Card(
            sku_id=item.sku_id,
            secret_ciphertext=card_vault.encrypt(delivered_secret),
            secret_fingerprint=card_vault.fingerprint(delivered_secret),
            status=CardStatus.DELIVERED,
            order_item_id=item.id,
            delivered_at=now,
        )
        session.add(card)
        order.status = OrderStatus.FULFILLED
        order.fulfilled_at = now
        return SupplierDelivery(
            user.telegram_user_id,
            order.public_id,
            item.product_name_snapshot,
            delivered_secret,
            True,
        )
