import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SupplierOrder


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
