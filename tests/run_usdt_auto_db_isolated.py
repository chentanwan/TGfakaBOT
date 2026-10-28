import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

import app.services.usdt_auto as auto
from app.core.config import get_settings
from app.core.security import card_vault
from app.db.base import CardStatus, OrderStatus, ProductStatus
from app.db.models import (
    SKU,
    AdminAuditLog,
    BalanceTopup,
    Card,
    Order,
    OrderItem,
    PluginPayment,
    Product,
    ProductPrice,
    User,
)
from app.db.session import SessionFactory
from app.plugins.registry import configure_plugins
from app.services.usdt_verification import VerificationResult

TAG = "USDT-AUTO-DB-TEST"


async def cleanup() -> None:
    async with SessionFactory() as session, session.begin():
        users = list(await session.scalars(select(User).where(User.display_name == TAG)))
        user_ids = [user.id for user in users]
        products = list(await session.scalars(select(Product).where(Product.name.like(f"{TAG}%"))))
        product_ids = [product.id for product in products]
        skus = list(await session.scalars(select(SKU).where(SKU.product_id.in_(product_ids))))
        sku_ids = [sku.id for sku in skus]
        orders = list(await session.scalars(select(Order).where(Order.user_id.in_(user_ids))))
        order_ids = [order.id for order in orders]
        payments = list(
            await session.scalars(
                select(PluginPayment).where(PluginPayment.order_id.in_(order_ids))
            )
        )
        await session.execute(
            delete(AdminAuditLog).where(
                AdminAuditLog.target_id.in_([str(payment.id) for payment in payments])
            )
        )
        await session.execute(delete(PluginPayment).where(PluginPayment.order_id.in_(order_ids)))
        await session.execute(delete(Card).where(Card.sku_id.in_(sku_ids)))
        await session.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        await session.execute(delete(Order).where(Order.id.in_(order_ids)))
        await session.execute(delete(BalanceTopup).where(BalanceTopup.user_id.in_(user_ids)))
        await session.execute(delete(ProductPrice).where(ProductPrice.sku_id.in_(sku_ids)))
        await session.execute(delete(SKU).where(SKU.id.in_(sku_ids)))
        await session.execute(delete(Product).where(Product.id.in_(product_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))


async def make_user(number: int) -> User:
    async with SessionFactory() as session, session.begin():
        user = User(
            telegram_user_id=-910000 - number,
            display_name=TAG,
            referral_code=f"AUTOTEST{number}",
        )
        session.add(user)
        await session.flush()
        session.expunge(user)
        return user


async def make_order(user: User, number: int) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with SessionFactory() as session, session.begin():
        product = Product(
            name=f"{TAG}-{number}", description="isolated fixture", status=ProductStatus.ACTIVE
        )
        session.add(product)
        await session.flush()
        sku = SKU(
            product_id=product.id,
            code=f"AUTO-SKU-{number}",
            name="Default",
            is_default=True,
            is_active=True,
            price_xtr=1,
            stars_enabled=False,
        )
        session.add(sku)
        await session.flush()
        session.add(
            ProductPrice(
                sku_id=sku.id,
                provider="usdt",
                currency="USD",
                amount_minor=100,
                is_active=True,
                metadata_json={},
            )
        )
        order = Order(
            public_id=f"AUTOPAY{number}{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            status=OrderStatus.PENDING_PAYMENT,
            payment_provider="usdt_bep20",
            currency="USD",
            total_amount=100,
            invoice_payload=uuid.uuid4().hex,
            reservation_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(order)
        await session.flush()
        item = OrderItem(
            order_id=order.id,
            sku_id=sku.id,
            product_name_snapshot=product.name,
            sku_name_snapshot="Default",
            unit_price=100,
            quantity=1,
            line_total=100,
        )
        session.add(item)
        await session.flush()
        secret = f"{TAG}-KEY-{number}"
        card = Card(
            sku_id=sku.id,
            secret_ciphertext=card_vault.encrypt(secret),
            secret_fingerprint=card_vault.fingerprint(secret),
            status=CardStatus.RESERVED,
            order_item_id=item.id,
            reserved_until=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(card)
        payment = PluginPayment(
            order_id=order.id,
            provider="usdt_bep20",
            status="proof_pending",
            currency="USD",
            amount_minor=100,
            transaction_id="0x" + f"{number:064x}",
            provider_data={"destination": "0x" + "a" * 40},
        )
        session.add(payment)
        await session.flush()
        return payment.id, order.id, card.id


async def confirmed(*args, **kwargs) -> VerificationResult:
    return VerificationResult("confirmed", confirmations=12, amount_minor=100)


async def rejected(*args, **kwargs) -> VerificationResult:
    return VerificationResult(
        "rejected", confirmations=12, amount_minor=200, reason="到账金额不匹配"
    )


async def main() -> None:
    await cleanup()
    settings = get_settings().model_copy(
        update={
            "usdt_auto_verify_enabled": True,
            "usdt_bep20_rpc_url": "http://mock.invalid",
            "usdt_bep20_token_contract": "0x" + "b" * 40,
            "usdt_bep20_address": "0x" + "a" * 40,
        }
    )
    auto.get_settings = lambda: settings
    configure_plugins(settings.plugin_modules)
    try:
        user = await make_user(1)
        async with SessionFactory() as session, session.begin():
            topup = BalanceTopup(
                user_id=user.id,
                method="usdt_bep20",
                amount_minor=100,
                proof="0x" + "1" * 64,
                status="pending",
            )
            session.add(topup)
            await session.flush()
            topup_id = topup.id
        auto.verify_usdt_payment = confirmed
        first, second = await asyncio.gather(
            auto.verify_topup(topup_id), auto.verify_topup(topup_id)
        )
        async with SessionFactory() as session:
            stored_user = await session.get(User, user.id)
            stored_topup = await session.get(BalanceTopup, topup_id)
            assert stored_user.balance_minor == 100
            assert stored_topup.status == "approved"
        assert first.state == "approved" and second.state == "approved"

        payment_id, order_id, card_id = await make_order(user, 2)
        first_order, second_order = await asyncio.gather(
            auto.verify_order(payment_id), auto.verify_order(payment_id)
        )
        async with SessionFactory() as session:
            order = await session.get(Order, order_id)
            card = await session.get(Card, card_id)
            payment = await session.get(PluginPayment, payment_id)
            assert order.status == OrderStatus.FULFILLED
            assert card.status == CardStatus.DELIVERED
            assert payment.status == "paid"
        delivered = [outcome for outcome in (first_order, second_order) if outcome.delivery]
        duplicates = [outcome for outcome in (first_order, second_order) if not outcome.delivery]
        assert len(delivered) == 1 and delivered[0].delivery.should_deliver
        assert len(duplicates) == 1 and duplicates[0].state == "paid"

        rejected_payment, rejected_order, rejected_card = await make_order(user, 3)
        auto.verify_usdt_payment = rejected
        outcome = await auto.verify_order(rejected_payment)
        async with SessionFactory() as session:
            order = await session.get(Order, rejected_order)
            card = await session.get(Card, rejected_card)
            payment = await session.get(PluginPayment, rejected_payment)
            assert order.status == OrderStatus.EXPIRED
            assert card.status == CardStatus.AVAILABLE
            assert payment.status == "rejected"
        assert outcome.state == "rejected"
        print("isolated_usdt_auto_db_ok")
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
