from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import normalize_language
from app.db.models import Order, User


@dataclass(slots=True)
class AccountSummary:
    user_id: uuid.UUID
    telegram_user_id: int
    username: str | None
    display_name: str
    language_code: str
    balance_minor: int
    points: int
    tier: str
    order_count: int
    total_spent_minor: int
    referral_code: str
    referral_count: int
    referral_reward_minor: int
    created_at: object


async def ensure_account(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None,
    display_name: str,
    telegram_language: str | None,
    referral_code: str | None = None,
) -> User:
    language = normalize_language(telegram_language)
    generated_code = f"REF_{telegram_user_id}"
    async with session.begin():
        await session.execute(
            insert(User)
            .values(
                telegram_user_id=telegram_user_id,
                username=username,
                display_name=display_name,
                language_code=language,
                referral_code=generated_code,
            )
            .on_conflict_do_update(
                index_elements=["telegram_user_id"],
                set_={"username": username, "display_name": display_name},
            )
        )
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        if user is None:
            raise RuntimeError("account creation failed")
        if referral_code and user.referred_by_user_id is None:
            referrer = await session.scalar(
                select(User).where(
                    func.lower(User.referral_code) == referral_code.lower(),
                    User.id != user.id,
                )
            )
            if referrer is not None:
                user.referred_by_user_id = referrer.id
        return user


async def get_account(session: AsyncSession, telegram_user_id: int) -> AccountSummary | None:
    user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        return None
    order_count = await session.scalar(
        select(func.count(Order.id)).where(Order.user_id == user.id)
    )
    referral_count = await session.scalar(
        select(func.count(User.id)).where(User.referred_by_user_id == user.id)
    )
    return AccountSummary(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        username=user.username,
        display_name=user.display_name or "",
        language_code=user.language_code,
        balance_minor=user.balance_minor,
        points=user.points,
        tier=user.tier,
        order_count=int(order_count or 0),
        total_spent_minor=user.total_spent_minor,
        referral_code=user.referral_code or f"REF_{user.telegram_user_id}",
        referral_count=int(referral_count or 0),
        referral_reward_minor=user.referral_reward_minor,
        created_at=user.created_at,
    )


async def set_account_language(
    session: AsyncSession, *, telegram_user_id: int, language_code: str
) -> str:
    language = normalize_language(language_code)
    async with session.begin():
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        if user is None:
            raise ValueError("account not found")
        user.language_code = language
    return language


@dataclass(slots=True)
class BalanceDelivery:
    public_id: str
    product_name: str
    quantity: int
    unit_price_minor: int
    total_amount_minor: int
    secrets: list[str]
    duplicate: bool = False


async def purchase_with_balance(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    sku_id: uuid.UUID,
    quantity: int,
    idempotency_key: str,
    points_per_usd: int,
) -> BalanceDelivery:
    import secrets as secret_module
    from datetime import UTC, datetime, timedelta

    from app.core.security import card_vault
    from app.db.base import CardStatus, OrderStatus, ProductStatus
    from app.db.models import SKU, Card, OrderItem, Product, ProductPrice

    if quantity < 1 or quantity > 100:
        raise ValueError("quantity must be between 1 and 100")
    async with session.begin():
        existing = await session.scalar(
            select(Order).where(Order.invoice_payload == idempotency_key).with_for_update()
        )
        if existing is not None:
            item = await session.scalar(select(OrderItem).where(OrderItem.order_id == existing.id))
            cards = (
                await session.scalars(
                    select(Card).where(Card.order_item_id == item.id).order_by(Card.id)
                )
            ).all()
            return BalanceDelivery(
                public_id=existing.public_id,
                product_name=item.product_name_snapshot,
                quantity=item.quantity,
                unit_price_minor=item.unit_price,
                total_amount_minor=item.line_total,
                secrets=[card_vault.decrypt(card.secret_ciphertext) for card in cards],
                duplicate=True,
            )
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        row = (
            await session.execute(
                select(SKU, Product, ProductPrice)
                .join(Product, Product.id == SKU.product_id)
                .join(
                    ProductPrice,
                    (ProductPrice.sku_id == SKU.id)
                    & (ProductPrice.provider == "balance")
                    & ProductPrice.is_active.is_(True),
                )
                .where(
                    SKU.id == sku_id,
                    SKU.is_active.is_(True),
                    Product.status == ProductStatus.ACTIVE,
                )
                .with_for_update(of=SKU)
            )
        ).one_or_none()
        if user is None or row is None:
            raise ValueError("account or product is unavailable")
        sku, product, price = row
        cards = (
            await session.scalars(
                select(Card)
                .where(Card.sku_id == sku.id, Card.status == CardStatus.AVAILABLE)
                .order_by(Card.id)
                .limit(quantity)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if len(cards) != quantity:
            raise ValueError("insufficient stock")
        total = price.amount_minor * quantity
        if user.balance_minor < total:
            raise ValueError("insufficient balance")
        now = datetime.now(UTC)
        order = Order(
            public_id=f"TG{now:%y%m%d}{secret_module.token_hex(3).upper()}",
            user_id=user.id,
            status=OrderStatus.FULFILLED,
            payment_provider="balance",
            currency="USD",
            total_amount=total,
            invoice_payload=idempotency_key,
            reservation_expires_at=now + timedelta(minutes=15),
            paid_at=now,
            fulfilled_at=now,
        )
        session.add(order)
        await session.flush()
        item = OrderItem(
            order_id=order.id,
            sku_id=sku.id,
            product_name_snapshot=product.name,
            sku_name_snapshot=sku.name,
            unit_price=price.amount_minor,
            quantity=quantity,
            line_total=total,
        )
        session.add(item)
        await session.flush()
        delivered = []
        for card in cards:
            card.status = CardStatus.DELIVERED
            card.order_item_id = item.id
            card.reserved_until = None
            card.delivered_at = now
            delivered.append(card_vault.decrypt(card.secret_ciphertext))
        user.balance_minor -= total
        user.total_spent_minor += total
        user.order_count += 1
        user.points += (total * max(0, points_per_usd)) // 100
        return BalanceDelivery(
            public_id=order.public_id,
            product_name=product.name,
            quantity=quantity,
            unit_price_minor=price.amount_minor,
            total_amount_minor=total,
            secrets=delivered,
        )
