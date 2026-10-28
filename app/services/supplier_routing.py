from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, SupplierOrder, SupplierProduct


@dataclass(frozen=True, slots=True)
class SupplierRoute:
    provider: str
    external_product_id: str
    unit_cost_minor: int
    currency: str


async def reserve_supplier_route(
    session: AsyncSession,
    *,
    order: Order,
    sku_id: uuid.UUID,
) -> SupplierRoute | None:
    """Select and snapshot the cheapest eligible supplier for a paid order."""
    reserved = await session.scalar(
        select(SupplierOrder).where(SupplierOrder.order_id == order.id).with_for_update()
    )
    if reserved is not None:
        mapping = await session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.sku_id == sku_id,
                SupplierProduct.provider == reserved.provider,
            )
        )
        if mapping is None:
            return None
        return SupplierRoute(
            reserved.provider,
            mapping.external_product_id,
            mapping.cost_amount_minor,
            mapping.cost_currency,
        )

    mapping = await session.scalar(
        select(SupplierProduct)
        .where(
            SupplierProduct.sku_id == sku_id,
            SupplierProduct.in_stock.is_(True),
            SupplierProduct.suspended_for_profit.is_(False),
            SupplierProduct.requires_customer_email.is_(False),
            SupplierProduct.cost_currency == order.currency,
        )
        .order_by(SupplierProduct.cost_amount_minor.asc(), SupplierProduct.provider.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if mapping is None:
        return None
    session.add(
        SupplierOrder(
            order_id=order.id,
            provider=mapping.provider,
            status="pending",
            cost_currency=mapping.cost_currency,
            response_data={
                "route_snapshot": {
                    "provider": mapping.provider,
                    "external_product_id": mapping.external_product_id,
                    "unit_cost_minor": mapping.cost_amount_minor,
                }
            },
        )
    )
    await session.flush()
    return SupplierRoute(
        mapping.provider,
        mapping.external_product_id,
        mapping.cost_amount_minor,
        mapping.cost_currency,
    )
