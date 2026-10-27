import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderItem, SupplierOrder


async def get_or_create_supplier_order(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    cost_currency: str,
) -> SupplierOrder:
    async with session.begin():
        supplier_order = await session.scalar(
            select(SupplierOrder)
            .where(SupplierOrder.order_id == order_id)
            .with_for_update()
        )
        if supplier_order is None:
            supplier_order = SupplierOrder(
                order_id=order_id,
                provider=provider,
                status="pending",
                cost_currency=cost_currency,
            )
            session.add(supplier_order)
            await session.flush()
        return supplier_order


async def save_supplier_order_result(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    external_order_id: str | None,
    status: str,
    cost_amount_minor: int | None,
    response_data: dict,
    last_error: str | None = None,
) -> None:
    async with session.begin():
        supplier_order = await session.scalar(
            select(SupplierOrder)
            .where(
                SupplierOrder.order_id == order_id,
                SupplierOrder.provider == provider,
            )
            .with_for_update()
        )
        if supplier_order is None:
            raise RuntimeError("supplier order was not reserved")
        supplier_order.external_order_id = external_order_id or supplier_order.external_order_id
        supplier_order.status = status
        supplier_order.cost_amount_minor = cost_amount_minor
        supplier_order.response_data = response_data
        supplier_order.last_error = last_error[:2000] if last_error else None

@dataclass(slots=True)
class SupplierOrderSummary:
    id: uuid.UUID
    order_id: uuid.UUID
    public_id: str
    product_name: str
    provider: str
    external_order_id: str | None
    status: str
    cost_currency: str
    cost_amount_minor: int | None
    last_error: str


async def list_supplier_exceptions(
    session: AsyncSession, *, provider: str, limit: int = 20
) -> list[SupplierOrderSummary]:
    rows = (
        await session.execute(
            select(SupplierOrder, Order, OrderItem)
            .join(Order, Order.id == SupplierOrder.order_id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                SupplierOrder.provider == provider,
                SupplierOrder.status.in_(["unknown", "failed", "pending", "paid"]),
            )
            .order_by(SupplierOrder.updated_at.asc())
            .limit(limit)
        )
    ).all()
    return [
        SupplierOrderSummary(
            id=supplier_order.id,
            order_id=order.id,
            public_id=order.public_id,
            product_name=item.product_name_snapshot,
            provider=supplier_order.provider,
            external_order_id=supplier_order.external_order_id,
            status=supplier_order.status,
            cost_currency=supplier_order.cost_currency,
            cost_amount_minor=supplier_order.cost_amount_minor,
            last_error=supplier_order.last_error or "",
        )
        for supplier_order, order, item in rows
    ]


async def record_supplier_recovery(
    session: AsyncSession,
    *,
    supplier_order_id: uuid.UUID,
    admin_telegram_id: int,
    note: str,
    status: str | None = None,
) -> SupplierOrder:
    if len(note.strip()) < 6:
        raise ValueError("恢复操作必须填写至少 6 个字符的核验说明")
    async with session.begin():
        supplier_order = await session.scalar(
            select(SupplierOrder)
            .where(SupplierOrder.id == supplier_order_id)
            .with_for_update()
        )
        if supplier_order is None:
            raise ValueError("上游采购订单不存在")
        if status:
            supplier_order.status = status
        supplier_order.recovery_note = note.strip()[:2000]
        supplier_order.recovered_at = datetime.now(UTC)
        supplier_order.recovered_by_telegram_id = admin_telegram_id
        return supplier_order
