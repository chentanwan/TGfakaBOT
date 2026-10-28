from functools import lru_cache

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    bot_token: str
    admin_ids: tuple[int, ...] = ()
    bot_language: str = "en"
    bot_username: str = ""
    brand_name: str = "TGfakaBOT"
    channel_handle: str = "@channel"
    channel_url: str = "https://t.me/channel"
    support_handle: str = "@cclchat_bot"
    support_url: str = "https://t.me/cclchat_bot"
    administrator_handle: str = "@admin"
    administrator_url: str = "https://t.me/admin"
    api_docs_url: str = "https://example.com/api-docs"
    referral_reward_percent: int = 0
    points_per_usd: int = 1
    database_url: str = "postgresql+asyncpg://tgfakabot:change-me@localhost:5432/tgfakabot"
    redis_url: str = "redis://localhost:6379/0"
    card_encryption_key: str
    card_fingerprint_key: str
    order_reservation_minutes: int = 15
    plugin_modules: tuple[str, ...] = ()
    plugin_http_host: str = "0.0.0.0"
    plugin_http_port: int = 8081
    log_level: str = "INFO"

    @field_validator("admin_ids", "plugin_modules", mode="before")
    @classmethod
    def parse_csv_tuple(cls, value: object, info: ValidationInfo) -> tuple:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            parts = tuple(part.strip() for part in value.split(",") if part.strip())
            if info.field_name == "admin_ids":
                return tuple(int(part) for part in parts)
            return parts
        return tuple(value)  # type: ignore[arg-type]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
