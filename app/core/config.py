from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: tuple[int, ...] = ()
    bot_language: str = "zh"
    support_url: str = ""
    database_url: str = "postgresql+asyncpg://tgfakabot:change-me@localhost:5432/tgfakabot"
    redis_url: str = "redis://localhost:6379/0"
    card_encryption_key: str
    card_fingerprint_key: str
    order_reservation_minutes: int = 15
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> tuple[int, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        return tuple(value)  # type: ignore[arg-type]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
