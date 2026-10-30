from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from plugins.prodseller.config import ProdsellerSettings


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    retry_after: int | None = None


class ProdsellerError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool | None = None,
        rate_limit: RateLimit | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = (
            status is None or status == 429 or status >= 500 if retryable is None else retryable
        )
        self.rate_limit = rate_limit or RateLimit()


class ProdsellerClient:
    def __init__(self, settings: ProdsellerSettings) -> None:
        self.settings = settings
        self.rate_limit = RateLimit()

    async def list_products(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/products")
        products = body.get("products")
        if not isinstance(products, list):
            raise ProdsellerError("上游商品响应格式无效", retryable=False)
        return [self._validate_product(item) for item in products]

    async def get_product(self, external_product_id: str) -> dict[str, Any]:
        path_id = quote(external_product_id, safe="")
        body = await self._request("GET", f"/products/{path_id}")
        return self._validate_product(body)

    async def balance(self) -> Decimal:
        body = await self._request("GET", "/balance")
        return self._amount(body.get("balance"))

    async def create_order(
        self,
        *,
        external_product_id: str,
        idempotency_key: str,
        quantity: int = 1,
        email: str | None = None,
        emails: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= quantity <= 100:
            raise ValueError("quantity must be between 1 and 100")
        if email and emails:
            raise ValueError("email and emails are mutually exclusive")
        if emails is not None and len(emails) != quantity:
            raise ValueError("emails length must equal quantity")
        payload: dict[str, Any] = {"productId": external_product_id, "quantity": quantity}
        if email:
            payload["email"] = email
        if emails is not None:
            payload["emails"] = emails
        body = await self._request(
            "POST",
            "/orders",
            payload=payload,
            extra_headers={"Idempotency-Key": idempotency_key[:100]},
        )
        return self._validate_order(body)

    async def list_orders(
        self, *, page: int = 1, limit: int = 50, status: str | None = None
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be positive")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if status is not None and status not in {"pending", "paid", "delivered", "failed"}:
            raise ValueError("unsupported order status")
        params: dict[str, str | int] = {"page": page, "limit": limit}
        if status:
            params["status"] = status
        body = await self._request("GET", "/orders", params=params)
        orders = body.get("orders")
        pagination = body.get("pagination")
        if not isinstance(orders, list) or not isinstance(pagination, dict):
            raise ProdsellerError("上游订单列表响应格式无效", retryable=False)
        return {
            "orders": [self._validate_order(order) for order in orders],
            "pagination": pagination,
        }

    async def get_order(self, external_order_id: str) -> dict[str, Any]:
        path_id = quote(external_order_id, safe="")
        body = await self._request("GET", f"/orders/{path_id}")
        return self._validate_order(body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"X-API-Key": self.settings.prodseller_api_key}
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self.settings.prodseller_base_url.rstrip('/')}{path}"
        try:
            async with ClientSession(timeout=ClientTimeout(total=20)) as client:
                async with client.request(
                    method, url, headers=headers, json=payload, params=params
                ) as response:
                    self.rate_limit = self._rate_limit(response)
                    body = await response.json(content_type=None)
                    if response.status >= 400:
                        message = body.get("error") if isinstance(body, dict) else None
                        raise ProdsellerError(
                            str(message or f"ProdSeller HTTP {response.status}"),
                            status=response.status,
                            rate_limit=self.rate_limit,
                        )
        except ProdsellerError:
            raise
        except (ClientError, TimeoutError, ValueError) as exc:
            raise ProdsellerError(f"ProdSeller 请求结果未知: {exc}") from exc
        if not isinstance(body, dict):
            raise ProdsellerError("ProdSeller 响应不是 JSON 对象", retryable=False)
        return body

    @classmethod
    def _validate_product(cls, product: Any) -> dict[str, Any]:
        if not isinstance(product, dict):
            raise ProdsellerError("上游商品格式无效", retryable=False)
        product_id = product.get("id")
        name = product.get("name")
        if not isinstance(product_id, str) or not product_id:
            raise ProdsellerError("上游商品缺少 ID", retryable=False)
        if not isinstance(name, str) or not name:
            raise ProdsellerError("上游商品缺少名称", retryable=False)
        cost = cls._amount(product.get("price"))
        stock = product.get("stock")
        if stock is not None and (
            not isinstance(stock, int) or isinstance(stock, bool) or stock < 0
        ):
            raise ProdsellerError("上游商品库存无效", retryable=False)
        return {
            **product,
            "id": product_id,
            "name": name,
            "price": cost,
            "stock": stock,
            "inStock": bool(product.get("inStock", stock is None or stock > 0)),
            "requiresEmailActivation": bool(product.get("requiresEmailActivation")),
        }

    @classmethod
    def _validate_order(cls, order: Any) -> dict[str, Any]:
        if not isinstance(order, dict):
            raise ProdsellerError("上游订单格式无效", retryable=False)
        order_id = order.get("orderId")
        status = order.get("status")
        quantity = order.get("quantity", 1)
        if not isinstance(order_id, str) or not order_id:
            raise ProdsellerError("上游订单缺少订单 ID", retryable=False)
        if status not in {"pending", "paid", "delivered", "failed"}:
            raise ProdsellerError("上游订单状态无效", retryable=False)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise ProdsellerError("上游订单数量无效", retryable=False)
        delivered_keys = order.get("deliveredKeys")
        delivered_key = order.get("deliveredKey")
        if delivered_keys is None:
            delivered_keys = (
                [delivered_key] if isinstance(delivered_key, str) and delivered_key else []
            )
        if not isinstance(delivered_keys, list) or not all(
            isinstance(value, str) and value for value in delivered_keys
        ):
            raise ProdsellerError("上游订单交付内容无效", retryable=False)
        return {
            **order,
            "quantity": quantity,
            "amount": cls._amount(order.get("amount")),
            "deliveredKeys": delivered_keys,
            "activationInstructions": cls._activation_instructions(
                order.get("activationInstructions")
            ),
        }

    @staticmethod
    def _activation_instructions(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ProdsellerError("上游激活说明格式无效", retryable=False)
        return [item for item in value if item]

    @staticmethod
    def _rate_limit(response: ClientResponse) -> RateLimit:
        def integer(name: str) -> int | None:
            raw = response.headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        return RateLimit(
            limit=integer("X-RateLimit-Limit"),
            remaining=integer("X-RateLimit-Remaining"),
            reset_at=integer("X-RateLimit-Reset"),
            retry_after=integer("Retry-After"),
        )

    @staticmethod
    def _amount(value: Any) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ProdsellerError("上游金额无效", retryable=False) from exc
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise ProdsellerError("上游金额精度无效", retryable=False)
        return amount
