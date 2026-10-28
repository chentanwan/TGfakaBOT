from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProdsellerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    prodseller_api_key: str
    prodseller_base_url: str = "https://prodseller.com/v1"
    prodseller_currency: str = "USD"
    prodseller_profit_percent: Decimal = Decimal("20")
    prodseller_profit_fixed: Decimal = Decimal("0.00")
    prodseller_product_sync_minutes: int = 60
    prodseller_order_poll_seconds: int = 30
    prodseller_rate_limit_reserve: int = 20

    @field_validator("prodseller_profit_percent", "prodseller_profit_fixed")
    @classmethod
    def validate_nonnegative_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("profit settings must be finite and nonnegative")
        return value

    @field_validator(
        "prodseller_product_sync_minutes",
        "prodseller_order_poll_seconds",
        "prodseller_rate_limit_reserve",
    )
    @classmethod
    def validate_positive_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("sync and poll intervals must be positive")
        return value


@lru_cache
def get_prodseller_settings() -> ProdsellerSettings:
    return ProdsellerSettings()  # type: ignore[call-arg]
