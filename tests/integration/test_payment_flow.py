import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import card_vault
from app.db.base import CardStatus, OrderStatus, PaymentStatus, ProductStatus
from app.db.models import SKU, Card, Order, PaymentCharge, Product
from app.services.orders import (
    create_order,
    match_and_fulfill_payment,
    settle_payment,
    submit_payment_receipt,
)
from app.services.payment_exceptions import fulfill_payment_exception


async def seed_product(session, *, card_count: int = 1):
    async with session.begin():
        product = Product(
            name="Integration Product", description="test", status=ProductStatus.ACTIVE
        )
        session.add(product)
        await session.flush()
        sku = SKU(
            product_id=product.id,
            code=f"test-{uuid.uuid4()}",
            name="Default",
            price_xtr=10,
            is_default=True,
            is_active=True,
        )
        session.add(sku)
        await session.flush()
        for index in range(card_count):
            secret = f"SECRET-{uuid.uuid4()}-{index}"
            session.add(
                Card(
                    sku_id=sku.id,
                    secret_ciphertext=card_vault.encrypt(secret),
                    secret_fingerprint=card_vault.fingerprint(secret),
                    status=CardStatus.AVAILABLE,
                )
            )
    return product


@pytest.mark.asyncio
async def test_concurrent_buyers_cannot_reserve_same_card(integration_engine, db_session):
    product = await seed_product(db_session)
    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def buy(user_id: int):
        async with factory() as session:
            try:
                return await create_order(
                    session,
                    telegram_user_id=user_id,
                    username=None,
                    display_name=str(user_id),
                    product_id=product.id,
                )
            except Exception:
                return None

    results = await asyncio.gather(buy(101), buy(102))
    assert sum(result is not None for result in results) == 1
    async with factory() as session:
        reserved = await session.scalar(
            select(func.count(Card.id)).where(Card.status == CardStatus.RESERVED)
        )
        assert reserved == 1


@pytest.mark.asyncio
async def test_replayed_charge_does_not_reveal_secret_again(db_session):
    product = await seed_product(db_session)
    created = await create_order(
        db_session,
        telegram_user_id=201,
        username="buyer",
        display_name="Buyer",
        product_id=product.id,
    )
    first = await settle_payment(
        db_session,
        telegram_user_id=201,
        invoice_payload=created.invoice_payload,
        currency="XTR",
        total_amount=created.total_amount,
        telegram_payment_charge_id="charge-replay",
        provider_payment_charge_id=None,
        raw_payload={},
    )
    second = await settle_payment(
        db_session,
        telegram_user_id=201,
        invoice_payload=created.invoice_payload,
        currency="XTR",
        total_amount=created.total_amount,
        telegram_payment_charge_id="charge-replay",
        provider_payment_charge_id=None,
        raw_payload={},
    )
    assert first.secret
    assert first.should_deliver is True
    assert second.secret == ""
    assert second.should_deliver is False


@pytest.mark.asyncio
async def test_admin_can_allocate_new_card_for_paid_exception(db_session):
    product = await seed_product(db_session, card_count=2)
    created = await create_order(
        db_session,
        telegram_user_id=301,
        username=None,
        display_name="Buyer",
        product_id=product.id,
    )
    async with db_session.begin():
        order = await db_session.scalar(
            select(Order).where(Order.invoice_payload == created.invoice_payload)
        )
        card = await db_session.scalar(select(Card).where(Card.order_item_id.is_not(None)))
        card.status = CardStatus.DISABLED
        card.order_item_id = None
        order.status = OrderStatus.PAYMENT_EXCEPTION
        order.paid_at = datetime.now(UTC)
        db_session.add(
            PaymentCharge(
                order_id=order.id,
                telegram_payment_charge_id="exception-charge",
                invoice_payload=created.invoice_payload,
                currency="XTR",
                total_amount=created.total_amount,
                status=PaymentStatus.SUCCEEDED,
                resolution_status="linked",
                raw_payload={},
                received_at=datetime.now(UTC),
            )
        )
    delivery = await fulfill_payment_exception(
        db_session,
        order_id=order.id,
        admin_telegram_user_id=999,
        allocate_new=True,
    )
    assert delivery.secret
    refreshed = await db_session.get(Order, order.id)
    assert refreshed.status == OrderStatus.FULFILLED


@pytest.mark.asyncio
async def test_receipt_commits_before_fulfillment(integration_engine, db_session):
    product = await seed_product(db_session)
    created = await create_order(
        db_session,
        telegram_user_id=401,
        username=None,
        display_name="Buyer",
        product_id=product.id,
    )
    receipt = await submit_payment_receipt(
        db_session,
        telegram_user_id=401,
        invoice_payload=created.invoice_payload,
        currency="XTR",
        total_amount=created.total_amount,
        telegram_payment_charge_id="two-phase-charge",
        provider_payment_charge_id=None,
        raw_payload={},
    )
    factory = async_sessionmaker(integration_engine, expire_on_commit=False)
    async with factory() as other_session:
        saved = await other_session.get(PaymentCharge, receipt.charge_id)
        assert saved is not None
        assert saved.resolution_status == "unmatched"
    async with factory() as matcher_session:
        delivered = await match_and_fulfill_payment(
            matcher_session, charge_id=receipt.charge_id
        )
    assert delivered.secret


@pytest.mark.asyncio
async def test_replay_fields_do_not_replace_first_receipt(db_session):
    first = await submit_payment_receipt(
        db_session,
        telegram_user_id=501,
        invoice_payload="first-payload",
        currency="XTR",
        total_amount=10,
        telegram_payment_charge_id="immutable-charge",
        provider_payment_charge_id=None,
        raw_payload={"version": 1},
    )
    replay = await submit_payment_receipt(
        db_session,
        telegram_user_id=999,
        invoice_payload="different-payload",
        currency="XTR",
        total_amount=999,
        telegram_payment_charge_id="immutable-charge",
        provider_payment_charge_id=None,
        raw_payload={"version": 2},
    )
    assert replay.charge_id == first.charge_id
    assert replay.duplicate is True
    charge = await db_session.get(PaymentCharge, first.charge_id)
    assert charge.telegram_user_id == 501
    assert charge.invoice_payload == "first-payload"
    assert charge.total_amount == 10
    assert charge.raw_payload == {"version": 1}
