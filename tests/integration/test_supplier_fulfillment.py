import pytest
from sqlalchemy import select

from app.db.base import CardStatus, ProductStatus
from app.db.models import SKU, Card, Product, ProductPrice, SupplierProduct
from app.services.orders import create_order
from app.services.supplier_fulfillment import fulfill_supplier_order


@pytest.mark.asyncio
async def test_supplier_backed_order_can_deliver_without_local_stock(db_session):
    async with db_session.begin():
        product = Product(name="Supplier product", description="test", status=ProductStatus.ACTIVE)
        db_session.add(product)
        await db_session.flush()
        sku = SKU(
            product_id=product.id,
            code="supplier-default",
            name="Default",
            price_xtr=1,
            stars_enabled=False,
            is_default=True,
            is_active=True,
        )
        db_session.add(sku)
        await db_session.flush()
        db_session.add_all(
            [
                SupplierProduct(
                    sku_id=sku.id,
                    provider="prodseller",
                    external_product_id="remote-1",
                    cost_currency="USD",
                    cost_amount_minor=400,
                    in_stock=True,
                    requires_customer_email=False,
                ),
                ProductPrice(
                    sku_id=sku.id,
                    provider="bepusdt",
                    currency="USD",
                    amount_minor=500,
                    is_active=True,
                ),
            ]
        )
    order = await create_order(
        db_session,
        telegram_user_id=9123,
        username=None,
        display_name="Supplier Buyer",
        product_id=product.id,
        payment_provider="bepusdt",
    )
    delivery = await fulfill_supplier_order(
        db_session,
        order_id=order.order_id,
        provider="prodseller",
        external_order_id="upstream-order-1",
        delivered_secret="UPSTREAM-SECRET",
        supplier_amount_minor=400,
        supplier_response={"status": "delivered"},
        sale_currency="USD",
        sale_amount_minor=500,
    )
    assert delivery.secret == "UPSTREAM-SECRET"
    card = await db_session.scalar(select(Card).where(Card.sku_id == sku.id))
    assert card.status == CardStatus.DELIVERED
