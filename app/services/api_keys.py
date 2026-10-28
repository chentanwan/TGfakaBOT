from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserApiCredential


@dataclass(slots=True)
class ApiCredentialSummary:
    prefix: str | None
    is_active: bool
    request_count: int
    last_used_at: object | None


def _hash_key(api_key: str) -> bytes:
    return hashlib.sha256(api_key.encode()).digest()


async def get_api_credential(
    session: AsyncSession, telegram_user_id: int
) -> ApiCredentialSummary:
    row = (
        await session.execute(
            select(UserApiCredential)
            .join(User, User.id == UserApiCredential.user_id)
            .where(User.telegram_user_id == telegram_user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return ApiCredentialSummary(None, False, 0, None)
    return ApiCredentialSummary(row.key_prefix, row.is_active, row.request_count, row.last_used_at)


async def rotate_api_key(session: AsyncSession, telegram_user_id: int) -> str:
    raw_key = f"tgf_{secrets.token_urlsafe(32)}"
    async with session.begin():
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        if user is None:
            raise ValueError("account not found")
        credential = await session.scalar(
            select(UserApiCredential)
            .where(UserApiCredential.user_id == user.id)
            .with_for_update()
        )
        if credential is None:
            credential = UserApiCredential(
                user_id=user.id,
                key_prefix=raw_key[:12],
                key_hash=_hash_key(raw_key),
                is_active=True,
            )
            session.add(credential)
        else:
            credential.key_prefix = raw_key[:12]
            credential.key_hash = _hash_key(raw_key)
            credential.is_active = True
            credential.request_count = 0
            credential.last_used_at = None
    return raw_key


async def authenticate_api_key(session: AsyncSession, api_key: str) -> User | None:
    key_hash = _hash_key(api_key)
    async with session.begin():
        credential = await session.scalar(
            select(UserApiCredential)
            .where(
                UserApiCredential.key_hash == key_hash,
                UserApiCredential.is_active.is_(True),
            )
            .with_for_update()
        )
        if credential is None:
            return None
        credential.request_count += 1
        credential.last_used_at = datetime.now(UTC)
        return await session.get(User, credential.user_id)
