from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsdtTransactionClaim


class TransactionAlreadyClaimed(ValueError):
    pass


async def reserve_transaction_claim(
    session: AsyncSession,
    *,
    network: str,
    transaction_id: str,
    source_type: str,
    source_id: uuid.UUID,
    destination: str,
    amount_minor: int,
) -> UsdtTransactionClaim:
    existing = await session.scalar(
        select(UsdtTransactionClaim)
        .where(
            UsdtTransactionClaim.network == network,
            UsdtTransactionClaim.transaction_id == transaction_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.source_type != source_type or existing.source_id != source_id:
            raise TransactionAlreadyClaimed("该链上交易已用于其他订单或充值")
        return existing
    claim = UsdtTransactionClaim(
        network=network,
        transaction_id=transaction_id,
        source_type=source_type,
        source_id=source_id,
        destination=destination,
        amount_minor=amount_minor,
        status="reserved",
        evidence={},
    )
    session.add(claim)
    await session.flush()
    return claim


async def update_transaction_claim(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: uuid.UUID,
    status: str,
    evidence: dict | None = None,
) -> None:
    claim = await session.scalar(
        select(UsdtTransactionClaim)
        .where(
            UsdtTransactionClaim.source_type == source_type,
            UsdtTransactionClaim.source_id == source_id,
        )
        .with_for_update()
    )
    if claim is None:
        return
    claim.status = status
    if evidence:
        claim.evidence = {**claim.evidence, **evidence}
