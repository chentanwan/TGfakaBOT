from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiohttp import ClientError, ClientSession, ClientTimeout

from plugins.bepusdt.config import BepusdtSettings
from plugins.bepusdt.signing import generate_signature


class GatewayError(Exception):
    pass


@dataclass(slots=True)
class GatewayCheckout:
    external_id: str
    checkout_url: str
    expires_at: datetime
    data: dict


async def create_transaction(
    settings: BepusdtSettings,
    *,
    merchant_order_id: str,
    amount_minor: int,
    product_name: str,
) -> GatewayCheckout:
    if not settings.bepusdt_public_base_url.startswith("https://"):
        raise GatewayError("BEPUSDT_PUBLIC_BASE_URL 必须为 HTTPS 公网地址")
    amount = float(Decimal(amount_minor) / Decimal(100))
    public_base = settings.bepusdt_public_base_url.rstrip("/")
    payload = {
        "order_id": merchant_order_id,
        "amount": amount,
        "fiat": settings.bepusdt_fiat,
        "trade_type": settings.bepusdt_trade_type,
        "name": product_name[:200],
        "notify_url": f"{public_base}/plugins/bepusdt/notify",
        "redirect_url": public_base,
        "timeout": settings.bepusdt_timeout_seconds,
    }
    payload["signature"] = generate_signature(payload, settings.bepusdt_api_token)
    url = f"{settings.bepusdt_base_url.rstrip('/')}/api/v1/order/create-transaction"
    try:
        async with ClientSession(timeout=ClientTimeout(total=20)) as client:
            async with client.post(url, json=payload) as response:
                body = await response.json(content_type=None)
                if response.status != 200:
                    raise GatewayError(f"BEpusdt HTTP {response.status}")
    except (ClientError, TimeoutError, ValueError) as exc:
        raise GatewayError(f"创建交易结果未知: {exc}") from exc
    try:
        if int(body.get("status_code", 0)) != 200 or not isinstance(body.get("data"), dict):
            raise ValueError(str(body.get("message") or "gateway rejected request"))
        data = body["data"]
        if data.get("order_id") != merchant_order_id:
            raise ValueError("gateway order id mismatch")
        external_id = str(data["trade_id"])
        checkout_url = str(data["payment_url"])
        expiration_seconds = max(120, int(data["expiration_time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise GatewayError(f"创建交易响应无效: {exc}") from exc
    return GatewayCheckout(
        external_id=external_id,
        checkout_url=checkout_url,
        expires_at=datetime.now(UTC) + timedelta(seconds=expiration_seconds),
        data=body,
    )
