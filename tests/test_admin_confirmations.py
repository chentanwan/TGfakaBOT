import uuid

import pytest

from app.services.admin_confirmations import consume_confirmation, create_confirmation


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int) -> None:
        assert ex == 300
        self.values[key] = value

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_confirmation_is_bound_and_single_use() -> None:
    redis = FakeRedis()
    order_id = uuid.uuid4()
    token = await create_confirmation(redis, admin_id=123, action="f", order_id=order_id)

    assert await consume_confirmation(
        redis, token=token, admin_id=123, action="f", order_id=order_id
    )
    assert not await consume_confirmation(
        redis, token=token, admin_id=123, action="f", order_id=order_id
    )


@pytest.mark.asyncio
async def test_wrong_admin_consumes_but_cannot_use_confirmation() -> None:
    redis = FakeRedis()
    order_id = uuid.uuid4()
    token = await create_confirmation(redis, admin_id=123, action="n", order_id=order_id)

    assert not await consume_confirmation(
        redis, token=token, admin_id=456, action="n", order_id=order_id
    )
