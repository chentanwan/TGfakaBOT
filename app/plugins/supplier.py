from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol


class SupplierApiError(Exception):
    """Base error contract implemented by supplier API adapters."""

    status: int | None
    retryable: bool


class SupplierAdapter(Protocol):
    """Network boundary required by an upstream supplier plugin."""

    async def list_products(self) -> list[dict[str, Any]]: ...

    async def get_product(self, external_product_id: str) -> dict[str, Any]: ...

    async def balance(self) -> Decimal: ...

    async def create_order(
        self,
        *,
        external_product_id: str,
        idempotency_key: str,
        quantity: int = 1,
        email: str | None = None,
        emails: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def list_orders(
        self, *, page: int = 1, limit: int = 50, status: str | None = None
    ) -> dict[str, Any]: ...

    async def get_order(self, external_order_id: str) -> dict[str, Any]: ...
