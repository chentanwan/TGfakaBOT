import asyncio

from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine


async def check_dependencies() -> None:
    settings = get_settings()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    redis = Redis.from_url(settings.redis_url)
    try:
        if not await redis.ping():
            raise RuntimeError("Redis ping failed")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(check_dependencies(), timeout=5))
