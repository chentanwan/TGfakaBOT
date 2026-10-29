import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.services.payment_settings import (
    get_payment_method,
    list_payment_methods,
    toggle_payment_method,
    update_payment_method,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def settings() -> Settings:
    return Settings(
        bot_token="test",
        card_encryption_key="x" * 44,
        card_fingerprint_key="x" * 44,
        usdt_bep20_address="0x" + "1" * 40,
        usdt_bep20_enabled=False,
    )


@pytest.mark.asyncio
async def test_edit_and_toggle_payment_method(session_factory):
    config = settings()
    async with session_factory() as session:
        methods = await list_payment_methods(session, config)
        assert [method.id for method in methods] == [
            "usdt_binance",
            "usdt_bep20",
            "usdt_trc20",
            "usdt_arbitrum",
        ]
        edited = await update_payment_method(session, config, "usdt_bep20", "label", "BSC USDT")
        assert edited.label == "BSC USDT"
        edited = await update_payment_method(
            session, config, "usdt_bep20", "minimum_topup_minor", 250
        )
        assert edited.minimum_topup_minor == 250
        toggled = await toggle_payment_method(session, config, "usdt_bep20")
        assert toggled.enabled and toggled.available
    async with session_factory() as session:
        stored = await get_payment_method(session, config, "usdt_bep20")
        assert stored.label == "BSC USDT"
        assert stored.minimum_topup_minor == 250
        assert stored.enabled


@pytest.mark.asyncio
async def test_cannot_enable_invalid_destination(session_factory):
    config = settings()
    async with session_factory() as session:
        await update_payment_method(session, config, "usdt_trc20", "destination", "")
        with pytest.raises(ValueError, match="收款信息"):
            await toggle_payment_method(session, config, "usdt_trc20")
