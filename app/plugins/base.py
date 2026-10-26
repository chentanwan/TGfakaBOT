from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot, Router
from aiogram.types import InlineKeyboardButton
from aiohttp import web


@dataclass(frozen=True, slots=True)
class PluginPrice:
    provider: str
    currency: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class PluginProduct:
    id: uuid.UUID
    name: str
    prices: dict[str, PluginPrice]


class Plugin(Protocol):
    name: str
    router: Router | None

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None: ...

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None: ...

    def register_http_routes(self, app: web.Application, bot: Bot) -> None: ...
