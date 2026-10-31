import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SKU, ProductPrice


class ProductPriceError(Exception):
    pass


async def _set_price(
    session: AsyncSession,
    *,
    sku_id: uuid.UUID,
    provider: str,
    currency: str,
    amount_minor: int | None,
    metadata_json: dict | None = None,
) -> None:
    price = await session.scalar(
        select(ProductPrice)
        .where(ProductPrice.sku_id == sku_id, ProductPrice.provider == provider)
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
                sku_id=sku_id,
                provider=provider,
                currency=currency,
                amount_minor=amount_minor,
                is_active=True,
                metadata_json=metadata_json or {},
            )
        )
    else:
        price.currency = currency
        price.amount_minor = amount_minor
        price.is_active = True
        if metadata_json is not None:
            price.metadata_json = metadata_json


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
        await _set_price(
            session,
            sku_id=sku.id,
            provider=provider,
            currency=currency,
            amount_minor=amount_minor,
        )


async def set_product_usd_price(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    amount_minor: int | None,
    metadata_json: dict | None = None,
) -> None:
    """Set one USD price and availability for both USDT and account balance."""
    async with session.begin():
        sku = await session.scalar(
            select(SKU)
            .where(SKU.product_id == product_id, SKU.is_default.is_(True))
            .with_for_update()
        )
        if sku is None:
            raise ProductPriceError("商品不存在")
        for provider in ("usdt", "balance"):
            await _set_price(
                session,
                sku_id=sku.id,
                provider=provider,
                currency="USD",
                amount_minor=amount_minor,
                metadata_json=metadata_json,
            )
