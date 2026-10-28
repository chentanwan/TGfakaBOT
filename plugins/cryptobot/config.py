from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CryptoBotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cryptobot_api_token: str
    cryptobot_api_url: str = "https://pay.crypt.bot/api"
    cryptobot_asset: str = "USDT"
    cryptobot_invoice_timeout_seconds: int = 900


@lru_cache
def get_cryptobot_settings() -> CryptoBotSettings:
    return CryptoBotSettings()  # type: ignore[call-arg]
