from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, select

from app.db.base import ProductStatus
from app.db.models import SKU, Product, ProductPrice, SupplierProduct
from app.db.session import SessionFactory
from plugins.prodseller.plugin import ProdsellerPlugin

PREFIX = "SUPPLIER-CATALOG-TEST"


async def cleanup() -> None:
    async with SessionFactory() as session, session.begin():
        products = (
            await session.scalars(select(Product).where(Product.name.like(f"{PREFIX}%")))
        ).all()
        ids = [product.id for product in products]
        if not ids:
            return
        sku_ids = list(await session.scalars(select(SKU.id).where(SKU.product_id.in_(ids))))
        if sku_ids:
            await session.execute(delete(ProductPrice).where(ProductPrice.sku_id.in_(sku_ids)))
            await session.execute(
                delete(SupplierProduct).where(SupplierProduct.sku_id.in_(sku_ids))
            )
            await session.execute(delete(SKU).where(SKU.id.in_(sku_ids)))
        await session.execute(delete(Product).where(Product.id.in_(ids)))


async def main() -> None:
    await cleanup()
    plugin = ProdsellerPlugin()
    remote = {
        "id": f"mock-{uuid.uuid4().hex}",
        "name": f"{PREFIX}-IMPORT",
        "description": "mock upstream product",
        "price": plugin.client._amount("1.25"),
        "inStock": True,
        "stock": 10,
        "requiresEmailActivation": False,
        "delivery": {"type": "instant"},
    }
    imported_id = await plugin._import_product(remote, admin_telegram_id=0)
    async with SessionFactory() as session:
        product = await session.get(Product, imported_id)
        sku = await session.scalar(select(SKU).where(SKU.product_id == imported_id))
        mapping = await session.scalar(
            select(SupplierProduct).where(SupplierProduct.sku_id == sku.id)
        )
        price = await session.scalar(
            select(ProductPrice).where(
                ProductPrice.sku_id == sku.id, ProductPrice.provider == "usdt"
            )
        )
        assert product.status == ProductStatus.DRAFT
        assert mapping.external_product_id == remote["id"] and mapping.in_stock
        assert price.is_active and price.amount_minor >= mapping.cost_amount_minor

    async with SessionFactory() as session, session.begin():
        local = Product(name=f"{PREFIX}-MAP", description="", status=ProductStatus.DRAFT)
        session.add(local)
        await session.flush()
        session.add(
            SKU(
                product_id=local.id,
                code=f"supplier-map-{uuid.uuid4().hex}",
                name="Default",
                price_xtr=1,
                stars_enabled=False,
                is_default=True,
                is_active=True,
            )
        )
        local_id = local.id
    remote2 = {**remote, "id": f"mock-{uuid.uuid4().hex}", "name": f"{PREFIX}-REMOTE2"}
    await plugin._map_existing_product(local_id, remote2, admin_telegram_id=0)
    async with SessionFactory() as session:
        sku = await session.scalar(select(SKU).where(SKU.product_id == local_id))
        mapping = await session.scalar(
            select(SupplierProduct).where(SupplierProduct.sku_id == sku.id)
        )
        price = await session.scalar(
            select(ProductPrice).where(
                ProductPrice.sku_id == sku.id, ProductPrice.provider == "usdt"
            )
        )
        assert mapping.external_product_id == remote2["id"]
        assert price.is_active and price.amount_minor >= mapping.cost_amount_minor
    await cleanup()
    print("isolated_prodseller_catalog_ok")


if __name__ == "__main__":
    asyncio.run(main())
