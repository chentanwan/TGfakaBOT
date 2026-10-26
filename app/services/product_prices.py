import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SKU, ProductPrice


class ProductPriceError(Exception):
    pass


async def set_product_plugin_price(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    provider: str,
    currency: str,
    amount_minor: int | None,
) -> None:
    async with session.begin():
        sku = await session.scalar(
            select(SKU)
            .where(SKU.product_id == product_id, SKU.is_default.is_(True))
            .with_for_update()
        )
        if sku is None:
            raise ProductPriceError("商品不存在")
        price = await session.scalar(
            select(ProductPrice)
            .where(ProductPrice.sku_id == sku.id, ProductPrice.provider == provider)
            .with_for_update()
        )
        if amount_minor is None:
            if price:
                price.is_active = False
            return
        if amount_minor < 1:
            raise ProductPriceError("价格必须大于 0")
        if price is None:
            session.add(
                ProductPrice(
                    sku_id=sku.id,
                    provider=provider,
                    currency=currency,
                    amount_minor=amount_minor,
                    is_active=True,
                )
            )
        else:
            price.currency = currency
            price.amount_minor = amount_minor
            price.is_active = True
