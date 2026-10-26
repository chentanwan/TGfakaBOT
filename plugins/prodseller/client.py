from decimal import Decimal, InvalidOperation
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from plugins.prodseller.config import ProdsellerSettings


class ProdsellerError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProdsellerClient:
    def __init__(self, settings: ProdsellerSettings) -> None:
        self.settings = settings

    async def list_products(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/products")
        products = body.get("products")
        if not isinstance(products, list):
            raise ProdsellerError("上游商品响应格式无效")
        return [self._validate_product(item) for item in products]

    async def balance(self) -> Decimal:
        body = await self._request("GET", "/balance")
        return self._amount(body.get("balance"))

    async def create_order(
        self,
        *,
        external_product_id: str,
        idempotency_key: str,
        email: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"productId": external_product_id, "quantity": 1}
        if email:
            payload["email"] = email
        body = await self._request(
            "POST",
            "/orders",
            payload=payload,
            extra_headers={"Idempotency-Key": idempotency_key[:100]},
        )
        return self._validate_order(body)

    async def get_order(self, external_order_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/orders/{external_order_id}")
        return self._validate_order(body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"X-API-Key": self.settings.prodseller_api_key}
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self.settings.prodseller_base_url.rstrip('/')}{path}"
        try:
            async with ClientSession(timeout=ClientTimeout(total=20)) as client:
                async with client.request(method, url, headers=headers, json=payload) as response:
                    body = await response.json(content_type=None)
                    if response.status >= 400:
                        message = body.get("error") if isinstance(body, dict) else None
                        raise ProdsellerError(
                            str(message or f"ProdSeller HTTP {response.status}"),
                            status=response.status,
                        )
        except ProdsellerError:
            raise
        except (ClientError, TimeoutError, ValueError) as exc:
            raise ProdsellerError(f"ProdSeller 请求结果未知: {exc}") from exc
        if not isinstance(body, dict):
            raise ProdsellerError("ProdSeller 响应不是 JSON 对象")
        return body

    @classmethod
    def _validate_product(cls, product: Any) -> dict[str, Any]:
        if not isinstance(product, dict):
            raise ProdsellerError("上游商品格式无效")
        product_id = product.get("id")
        name = product.get("name")
        if not isinstance(product_id, str) or not product_id:
            raise ProdsellerError("上游商品缺少 ID")
        if not isinstance(name, str) or not name:
            raise ProdsellerError("上游商品缺少名称")
        # The live API may include finalPrice; documented `price` remains authoritative.
        cost = cls._amount(product.get("price"))
        return {
            **product,
            "id": product_id,
            "name": name,
            "price": cost,
            "inStock": bool(product.get("inStock")),
            "requiresEmailActivation": bool(product.get("requiresEmailActivation")),
        }

    @classmethod
    def _validate_order(cls, order: Any) -> dict[str, Any]:
        if not isinstance(order, dict):
            raise ProdsellerError("上游订单格式无效")
        order_id = order.get("orderId")
        status = order.get("status")
        if not isinstance(order_id, str) or not order_id:
            raise ProdsellerError("上游订单缺少订单 ID")
        if status not in {"pending", "paid", "delivered", "failed"}:
            raise ProdsellerError("上游订单状态无效")
        return {**order, "amount": cls._amount(order.get("amount"))}

    @staticmethod
    def _amount(value: Any) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ProdsellerError("上游金额无效") from exc
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise ProdsellerError("上游金额精度无效")
        return amount
