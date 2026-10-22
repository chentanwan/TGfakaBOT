import os

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://tgfakabot:test-password@127.0.0.1:55432/tgfakabot_test",
)


@pytest_asyncio.fixture(scope="session")
async def integration_engine():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 to run PostgreSQL integration tests")
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    try:
        alembic = Config("alembic.ini")
        alembic.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
        command.upgrade(alembic, "head")
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        yield engine
        await engine.dispose()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url


@pytest_asyncio.fixture
async def db_session(integration_engine):
    factory = async_sessionmaker(integration_engine, expire_on_commit=False)
    async with integration_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
    async with factory() as session:
        yield session
