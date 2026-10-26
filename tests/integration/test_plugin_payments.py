import pytest
from sqlalchemy import select

from app.db.base import CardStatus, ProductStatus
from app.db.models import SKU, Card, PluginPayment, Product, ProductPrice
from app.services.admin import import_cards
from app.services.orders import create_order
from app.services.plugin_payments import fulfill_plugin_payment, reserve_plugin_payment


@pytest.mark.asyncio
async def test_plugin_payment_fulfills_only_once(db_session):
    async with db_session.begin():
        product = Product(name="Plugin product", description="test", status=ProductStatus.ACTIVE)
        db_session.add(product)
        await db_session.flush()
        sku = SKU(
            product_id=product.id,
            code="plugin-default",
            name="Default",
            price_xtr=50,
            is_default=True,
            is_active=True,
        )
        db_session.add(sku)
        await db_session.flush()
        db_session.add(
            ProductPrice(
                sku_id=sku.id,
                provider="bepusdt",
                currency="CNY",
                amount_minor=990,
                is_active=True,
            )
        )
    await import_cards(db_session, product_id=product.id, secrets=["PLUGIN-SECRET"])
    order = await create_order(
        db_session,
        telegram_user_id=8801,
        username=None,
        display_name="Plugin Buyer",
        product_id=product.id,
        payment_provider="bepusdt",
    )
    await reserve_plugin_payment(db_session, order_id=order.order_id, provider="bepusdt")
    first = await fulfill_plugin_payment(
        db_session,
        provider="bepusdt",
        merchant_order_id=order.public_id,
        transaction_id="chain-tx-1",
        amount_minor=990,
        event={"status": 2},
    )
    second = await fulfill_plugin_payment(
        db_session,
        provider="bepusdt",
        merchant_order_id=order.public_id,
        transaction_id="chain-tx-1",
        amount_minor=990,
        event={"status": 2},
    )
    assert first.secret == "PLUGIN-SECRET"
    assert first.should_deliver is True
    assert second.secret == ""
    assert second.should_deliver is False
    payment = await db_session.scalar(
        select(PluginPayment).where(PluginPayment.order_id == order.order_id)
    )
    assert payment.status == "paid"
    card = await db_session.scalar(select(Card).where(Card.sku_id == sku.id))
    assert card.status == CardStatus.DELIVERED
    assert (await db_session.get(Product, product.id)).status == ProductStatus.ACTIVE
