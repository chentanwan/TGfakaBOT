import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select

from app.db.base import CardStatus, OrderStatus
from app.db.models import Card, Order, OrderItem, PaymentCharge
from app.db.session import SessionFactory

logger = logging.getLogger(__name__)


async def release_expired_orders_once() -> int:
    now = datetime.now(UTC)
    released = 0
    async with SessionFactory() as session, session.begin():
        orders = (
            await session.scalars(
                select(Order)
                .where(
                    Order.status == OrderStatus.PENDING_PAYMENT,
                    Order.precheckout_approved_at.is_(None),
                    Order.reservation_expires_at < now,
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for order in orders:
            paid = await session.scalar(
                select(PaymentCharge.id).where(PaymentCharge.order_id == order.id)
            )
            if paid:
                continue
            item_id = await session.scalar(
                select(OrderItem.id).where(OrderItem.order_id == order.id)
            )
            card = await session.scalar(
                select(Card).where(Card.order_item_id == item_id).with_for_update()
            )
            if card and card.status == CardStatus.RESERVED:
                card.status = CardStatus.AVAILABLE
                card.order_item_id = None
                card.reserved_until = None
            order.status = OrderStatus.EXPIRED
            released += 1
    return released


async def cleanup_loop(_: Bot) -> None:
    while True:
        try:
            count = await release_expired_orders_once()
            if count:
                logger.info("released %s expired orders", count)
        except Exception:
            logger.exception("expired order cleanup failed")
        await asyncio.sleep(60)
