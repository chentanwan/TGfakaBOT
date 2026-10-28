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
