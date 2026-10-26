from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BepusdtSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bepusdt_base_url: str
    bepusdt_api_token: str
    bepusdt_public_base_url: str
    bepusdt_trade_type: str = "usdt.trc20"
    bepusdt_fiat: str = "CNY"
    bepusdt_timeout_seconds: int = 900


@lru_cache
def get_bepusdt_settings() -> BepusdtSettings:
    return BepusdtSettings()  # type: ignore[call-arg]
