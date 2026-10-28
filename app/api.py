from __future__ import annotations

import time
from collections import defaultdict, deque

from aiohttp import web

from app.db.session import SessionFactory
from app.services.api_keys import authenticate_api_key
from app.services.orders import list_products

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 60
_requests: dict[str, deque[float]] = defaultdict(deque)


def _client_key(request: web.Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-API-Key", "").strip() or None


def _allow(key: str) -> bool:
    now = time.monotonic()
    queue = _requests[key]
    while queue and now - queue[0] >= _WINDOW_SECONDS:
        queue.popleft()
    if len(queue) >= _MAX_REQUESTS:
        return False
    queue.append(now)
    return True


@web.middleware
async def api_auth(request: web.Request, handler):
    if not request.path.startswith("/api/v1/"):
        return await handler(request)
    api_key = _client_key(request)
    if not api_key:
        return web.json_response({"error": "missing_api_key"}, status=401)
    if not _allow(api_key):
        return web.json_response({"error": "rate_limited"}, status=429)
    async with SessionFactory() as session:
        user = await authenticate_api_key(session, api_key)
    if user is None:
        return web.json_response({"error": "invalid_api_key"}, status=401)
    request["api_user"] = user
    return await handler(request)


async def account(request: web.Request) -> web.Response:
    user = request["api_user"]
    return web.json_response(
        {
            "telegram_id": user.telegram_user_id,
            "balance": f"{user.balance_minor / 100:.2f}",
            "currency": "USD",
            "points": user.points,
            "tier": user.tier,
            "order_count": user.order_count,
            "total_spent": f"{user.total_spent_minor / 100:.2f}",
        }
    )


async def products(request: web.Request) -> web.Response:
    async with SessionFactory() as session:
        rows = await list_products(session)
    return web.json_response(
        {
            "products": [
                {
                    "id": str(product.id),
                    "name": product.name,
                    "description": product.description,
                    "category": product.category_name,
                    "sku_id": str(product.sku_id),
                    "plan": product.sku_name,
                    "price": (
                        f"{product.balance_price_minor / 100:.2f}"
                        if product.balance_price_minor is not None
                        else None
                    ),
                    "currency": "USD",
                    "stock": product.stock,
                    "available": product.stock > 0 or product.supplier_stock,
                }
                for product in rows
            ]
        }
    )


def register_api_routes(app: web.Application) -> None:
    app.middlewares.append(api_auth)
    app.router.add_get("/api/v1/account", account)
    app.router.add_get("/api/v1/products", products)
