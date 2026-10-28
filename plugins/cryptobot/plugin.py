from __future__ import annotations

import html
import json
import logging
import uuid

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiohttp import web

from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.topups import review_topup, submit_topup
from plugins.cryptobot.config import get_cryptobot_settings
from plugins.cryptobot.gateway import CryptoBotError, create_invoice
from plugins.cryptobot.signing import verify_webhook

logger = logging.getLogger(__name__)
PROVIDER = "cryptobot"


class CryptoTopupState(StatesGroup):
    amount = State()


class CryptoBotPlugin:
    name = PROVIDER

    def __init__(self) -> None:
        self.settings = get_cryptobot_settings()
        self.router = Router(name="plugin_cryptobot")
        self.router.callback_query.register(
            self._start_topup, F.data == "plugin:cryptobot:topup"
        )
        self.router.message.register(self._create_topup, CryptoTopupState.amount)

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return None

    def admin_home_buttons(self) -> list[list[InlineKeyboardButton]]:
        return []

    async def startup(self, bot: Bot) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def fulfill_paid_order(self, bot: Bot, delivered: object) -> bool:
        return False

    def register_http_routes(self, app: web.Application, bot: Bot) -> None:
        app["cryptobot_bot"] = bot
        app.router.add_post("/plugins/cryptobot/notify", self._notify)

    async def _start_topup(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(CryptoTopupState.amount)
        await callback.message.answer(
            "🤖 <b>CryptoBot</b>\n\nEnter the USD amount to top up (minimum $0.10):"
        )

    async def _create_topup(self, message: Message, state: FSMContext) -> None:
        try:
            amount_minor = round(float((message.text or "").strip()) * 100)
            if amount_minor < 10:
                raise ValueError
        except ValueError:
            await message.answer("Enter a valid amount of at least $0.10.")
            return
        payload = f"cryptobot:{message.from_user.id}:{uuid.uuid4()}"
        try:
            invoice = await create_invoice(
                self.settings, amount_minor=amount_minor, payload=payload
            )
            async with SessionFactory() as session:
                record = await submit_topup(
                    session,
                    telegram_user_id=message.from_user.id,
                    method=PROVIDER,
                    amount_minor=amount_minor,
                    proof=str(invoice.invoice_id),
                )
        except (CryptoBotError, ValueError) as exc:
            await message.answer(f"❌ {html.escape(str(exc))}")
            return
        await state.clear()
        await message.answer(
            "🤖 <b>CryptoBot invoice</b>\n"
            f"Amount: ${amount_minor / 100:.2f}\n"
            "The balance will be credited automatically after payment.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Pay invoice ↗", url=invoice.pay_url)],
                    [InlineKeyboardButton(text="« Main menu", callback_data="shop:home")],
                ]
            ),
        )
        logger.info("CryptoBot invoice %s mapped to topup %s", invoice.invoice_id, record.id)

    async def _notify(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_webhook(
            self.settings.cryptobot_api_token,
            body,
            request.headers.get("crypto-pay-api-signature"),
        ):
            return web.Response(status=401, text="invalid signature")
        try:
            update = json.loads(body)
            if update.get("update_type") != "invoice_paid":
                return web.Response(text="ok")
            invoice = update["payload"]
            invoice_id = str(invoice["invoice_id"])
            amount_minor = round(float(invoice["amount"]) * 100)
            async with SessionFactory() as session:
                from sqlalchemy import select

                from app.db.models import BalanceTopup

                record = await session.scalar(
                    select(BalanceTopup).where(
                        BalanceTopup.method == PROVIDER,
                        BalanceTopup.proof == invoice_id,
                    )
                )
            if record is None or record.amount_minor != amount_minor:
                logger.warning("Unknown or mismatched CryptoBot invoice %s", invoice_id)
                return web.Response(status=409, text="invoice mismatch")
            async with SessionFactory() as session:
                approved, telegram_user_id, duplicate = await review_topup(
                    session,
                    topup_id=record.id,
                    admin_telegram_id=0,
                    approve=True,
                )
            if not duplicate:
                bot: Bot = request.app["cryptobot_bot"]
                await bot.send_message(
                    telegram_user_id,
                    f"✅ CryptoBot top-up received: ${approved.amount_minor / 100:.2f}",
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Invalid CryptoBot callback")
            return web.Response(status=400, text="invalid callback")
        return web.Response(text="ok")
