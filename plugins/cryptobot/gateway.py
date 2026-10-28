from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import aiohttp

from plugins.cryptobot.config import CryptoBotSettings


class CryptoBotError(RuntimeError):
    pass


@dataclass(slots=True)
class CryptoInvoice:
    invoice_id: int
    pay_url: str
    amount: Decimal
    status: str


async def create_invoice(
    settings: CryptoBotSettings, *, amount_minor: int, payload: str
) -> CryptoInvoice:
    headers = {"Crypto-Pay-API-Token": settings.cryptobot_api_token}
    body = {
        "asset": settings.cryptobot_asset,
        "amount": f"{Decimal(amount_minor) / Decimal(100):.2f}",
        "payload": payload,
        "expires_in": settings.cryptobot_invoice_timeout_seconds,
        "description": "Account balance top-up",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{settings.cryptobot_api_url.rstrip('/')}/createInvoice",
            headers=headers,
            json=body,
        ) as response:
            data = await response.json(content_type=None)
    if not data.get("ok") or not isinstance(data.get("result"), dict):
        raise CryptoBotError(str(data.get("error") or "CryptoBot invoice creation failed"))
    result = data["result"]
    return CryptoInvoice(
        invoice_id=int(result["invoice_id"]),
        pay_url=str(result.get("bot_invoice_url") or result.get("pay_url")),
        amount=Decimal(str(result["amount"])),
        status=str(result["status"]),
    )
