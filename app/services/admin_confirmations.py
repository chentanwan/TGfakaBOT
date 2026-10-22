import secrets
import uuid

from redis.asyncio import Redis

TOKEN_TTL_SECONDS = 300


def _key(token: str) -> str:
    return f"tgfakabot:admin-confirm:{token}"


async def create_confirmation(
    redis: Redis, *, admin_id: int, action: str, order_id: uuid.UUID
) -> str:
    token = secrets.token_urlsafe(8)
    value = f"{admin_id}:{action}:{order_id}"
    await redis.set(_key(token), value, ex=TOKEN_TTL_SECONDS)
    return token


async def consume_confirmation(
    redis: Redis,
    *,
    token: str,
    admin_id: int,
    action: str,
    order_id: uuid.UUID,
) -> bool:
    key = _key(token)
    value = await redis.getdel(key)
    if value is None:
        return False
    if isinstance(value, bytes):
        value = value.decode()
    return value == f"{admin_id}:{action}:{order_id}"
