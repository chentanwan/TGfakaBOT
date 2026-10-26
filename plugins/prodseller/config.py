from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProdsellerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    prodseller_api_key: str
    prodseller_base_url: str = "https://prodseller.com/v1"
    prodseller_currency: str = "USD"
    prodseller_sale_markup_percent: int = 20
    prodseller_poll_seconds: int = 30


@lru_cache
def get_prodseller_settings() -> ProdsellerSettings:
    return ProdsellerSettings()  # type: ignore[call-arg]
