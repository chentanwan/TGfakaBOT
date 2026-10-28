from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BalanceTopup, User
from app.services.audit import record_admin_action
from app.services.usdt_methods import normalize_and_validate_proof

ALLOWED_USDT_TOPUP_METHODS = frozenset(
    {"usdt_binance", "usdt_bep20", "usdt_trc20", "usdt_arbitrum"}
)


@dataclass(slots=True)
class TopupSummary:
    id: uuid.UUID
    telegram_user_id: int
    display_name: str
    method: str
    amount_minor: int
    proof: str
    status: str
    created_at: object


async def submit_topup(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    method: str,
    amount_minor: int,
    proof: str,
) -> BalanceTopup:
    if method not in ALLOWED_USDT_TOPUP_METHODS:
        raise ValueError("unsupported top-up method")
    if amount_minor < 10:
        raise ValueError("minimum top-up amount is $0.10")
    proof = normalize_and_validate_proof(method, proof)
    try:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
            if user is None:
                raise ValueError("account not found")
            record = BalanceTopup(
                user_id=user.id,
                method=method,
                amount_minor=amount_minor,
                proof=proof,
                status="pending",
            )
            session.add(record)
            await session.flush()
            return record
    except IntegrityError as exc:
        raise ValueError("this payment proof has already been submitted") from exc


async def list_pending_topups(
    session: AsyncSession, *, limit: int = 20
) -> list[TopupSummary]:
    rows = (
        await session.execute(
            select(BalanceTopup, User)
            .join(User, User.id == BalanceTopup.user_id)
            .where(BalanceTopup.status == "pending")
            .order_by(BalanceTopup.created_at.asc())
            .limit(limit)
        )
    ).all()
    return [
        TopupSummary(
            id=record.id,
            telegram_user_id=user.telegram_user_id,
            display_name=user.display_name or str(user.telegram_user_id),
            method=record.method,
            amount_minor=record.amount_minor,
            proof=record.proof,
            status=record.status,
            created_at=record.created_at,
        )
        for record, user in rows
    ]


async def review_topup(
    session: AsyncSession,
    *,
    topup_id: uuid.UUID,
    admin_telegram_id: int,
    approve: bool,
    rejection_reason: str | None = None,
) -> tuple[BalanceTopup, int, bool]:
    async with session.begin():
        record = await session.scalar(
            select(BalanceTopup).where(BalanceTopup.id == topup_id).with_for_update()
        )
        if record is None:
            raise ValueError("top-up record not found")
        user = await session.scalar(
            select(User).where(User.id == record.user_id).with_for_update()
        )
        if user is None:
            raise ValueError("account not found")
        if record.status != "pending":
            return record, user.telegram_user_id, True
        if approve:
            user.balance_minor += record.amount_minor
            record.status = "approved"
        else:
            reason = (rejection_reason or "Rejected by administrator").strip()
            record.status = "rejected"
            record.rejection_reason = reason[:2000]
        record.reviewed_at = datetime.now(UTC)
        record.reviewed_by_telegram_id = admin_telegram_id
        if admin_telegram_id:
            await record_admin_action(
                session, admin_telegram_id=admin_telegram_id, action="topup_review",
                target_type="balance_topup", target_id=str(record.id),
                details={"approved": approve, "amount_minor": record.amount_minor},
            )
        return record, user.telegram_user_id, False


async def list_user_topups(
    session: AsyncSession, *, telegram_user_id: int, limit: int = 10
) -> list[TopupSummary]:
    rows = (
        await session.execute(
            select(BalanceTopup, User)
            .join(User, User.id == BalanceTopup.user_id)
            .where(User.telegram_user_id == telegram_user_id)
            .order_by(BalanceTopup.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        TopupSummary(
            id=record.id, telegram_user_id=user.telegram_user_id,
            display_name=user.display_name or str(user.telegram_user_id),
            method=record.method, amount_minor=record.amount_minor,
            proof=record.proof, status=record.status, created_at=record.created_at,
        )
        for record, user in rows
    ]
