from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import BalanceTopup, Order, PluginPayment, User
from app.db.session import SessionFactory
from app.plugins.registry import get_plugin_registry
from app.services.plugin_payments import (
    PluginDelivery,
    fulfill_plugin_payment,
    reject_manual_usdt_payment,
)
from app.services.topups import review_topup
from app.services.usdt_verification import (
    VerificationError,
    automatic_verification_available,
    verify_usdt_payment,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutoVerificationOutcome:
    state: str
    delivery: PluginDelivery | None = None
    telegram_user_id: int | None = None
    reference: str | None = None
    reason: str | None = None


async def verify_topup(topup_id) -> AutoVerificationOutcome:
    settings = get_settings()
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(BalanceTopup, User)
                .join(User, User.id == BalanceTopup.user_id)
                .where(BalanceTopup.id == topup_id)
            )
        ).one_or_none()
        if row is None:
            return AutoVerificationOutcome("missing")
        topup, user = row
        if topup.status != "pending":
            return AutoVerificationOutcome(topup.status, telegram_user_id=user.telegram_user_id)
        method, proof, amount = topup.method, topup.proof, topup.amount_minor
    if not automatic_verification_available(settings, method):
        return AutoVerificationOutcome("unsupported", reason="未配置自动核验接口")
    destination = _destination(settings, method)
    result = await verify_usdt_payment(
        settings,
        method_id=method,
        proof=proof,
        destination=destination,
        expected_amount_minor=amount,
    )
    if result.state == "rejected":
        async with SessionFactory() as session:
            record, telegram_user_id, _ = await review_topup(
                session,
                topup_id=topup_id,
                admin_telegram_id=0,
                approve=False,
                rejection_reason=f"Automatic verification: {result.reason}",
            )
        return AutoVerificationOutcome(
            "rejected",
            telegram_user_id=telegram_user_id,
            reference=str(record.id),
            reason=result.reason,
        )
    if result.state != "confirmed":
        return AutoVerificationOutcome(result.state, reason=result.reason)
    async with SessionFactory() as session:
        record, telegram_user_id, duplicate = await review_topup(
            session,
            topup_id=topup_id,
            admin_telegram_id=0,
            approve=True,
        )
    return AutoVerificationOutcome(
        "approved" if not duplicate else "approved_duplicate",
        telegram_user_id=telegram_user_id,
        reference=str(record.id),
    )


async def verify_order(payment_id) -> AutoVerificationOutcome:
    settings = get_settings()
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(PluginPayment, Order)
                .join(Order, Order.id == PluginPayment.order_id)
                .where(PluginPayment.id == payment_id)
            )
        ).one_or_none()
        if row is None:
            return AutoVerificationOutcome("missing")
        payment, order = row
        if payment.status == "paid":
            return AutoVerificationOutcome("paid", reference=order.public_id)
        if payment.status != "proof_pending" or not payment.transaction_id:
            return AutoVerificationOutcome(payment.status, reference=order.public_id)
        method = payment.provider
        proof = payment.transaction_id
        amount = payment.amount_minor
        public_id = order.public_id
        destination = str(payment.provider_data.get("destination") or "")
    if not automatic_verification_available(settings, method):
        return AutoVerificationOutcome("unsupported", reference=public_id)
    result = await verify_usdt_payment(
        settings,
        method_id=method,
        proof=proof,
        destination=destination,
        expected_amount_minor=amount,
    )
    if result.state == "rejected":
        async with SessionFactory() as session:
            telegram_user_id, _, _ = await reject_manual_usdt_payment(
                session, payment_id=payment_id, admin_telegram_id=0
            )
        return AutoVerificationOutcome(
            "rejected",
            telegram_user_id=telegram_user_id,
            reference=public_id,
            reason=result.reason,
        )
    if result.state != "confirmed":
        return AutoVerificationOutcome(result.state, reference=public_id, reason=result.reason)
    async with SessionFactory() as session:
        delivery = await fulfill_plugin_payment(
            session,
            provider=method,
            merchant_order_id=public_id,
            transaction_id=proof,
            amount_minor=amount,
            event={"type": "automatic_chain_verification", "confirmations": result.confirmations},
            provider_data={"automatically_verified": True, "confirmations": result.confirmations},
        )
    return AutoVerificationOutcome("paid", delivery=delivery, reference=public_id)


def _destination(settings, method: str) -> str:
    return {
        "usdt_bep20": settings.usdt_bep20_address,
        "usdt_trc20": settings.usdt_trc20_address,
        "usdt_arbitrum": settings.usdt_arbitrum_address,
        "usdt_binance": settings.binance_pay_uid,
    }[method]


async def _notify_delivery(bot: Bot, outcome: AutoVerificationOutcome) -> None:
    delivery = outcome.delivery
    if delivery is None:
        return
    if delivery.needs_supplier and delivery.order_id:
        for plugin in get_plugin_registry().supplier_plugins:
            if await plugin.fulfill_paid_order(bot, delivery):
                return
    elif delivery.should_deliver:
        await bot.send_message(
            delivery.telegram_user_id,
            "✅ USDT 已自动核验到账\n"
            f"Order: <code>{delivery.public_id}</code>\n"
            f"Product: {delivery.product_name}\n"
            f"Secret: <code>{delivery.secret}</code>",
        )
    elif delivery.exception_message:
        await bot.send_message(delivery.telegram_user_id, delivery.exception_message)


async def usdt_verification_loop(bot: Bot) -> None:
    settings = get_settings()
    while True:
        try:
            if settings.usdt_auto_verify_enabled:
                async with SessionFactory() as session:
                    topup_ids = list(
                        await session.scalars(
                            select(BalanceTopup.id)
                            .where(BalanceTopup.status == "pending")
                            .order_by(BalanceTopup.created_at.asc())
                            .limit(50)
                        )
                    )
                    payment_ids = list(
                        await session.scalars(
                            select(PluginPayment.id)
                            .where(
                                PluginPayment.status == "proof_pending",
                                PluginPayment.provider.startswith("usdt_"),
                            )
                            .order_by(PluginPayment.created_at.asc())
                            .limit(50)
                        )
                    )
                for topup_id in topup_ids:
                    try:
                        outcome = await verify_topup(topup_id)
                        if outcome.state == "approved" and outcome.telegram_user_id:
                            await bot.send_message(
                                outcome.telegram_user_id,
                                "✅ USDT 充值已自动核验到账\n"
                                f"Reference: <code>{outcome.reference}</code>",
                            )
                    except Exception:
                        logger.exception("USDT top-up verification failed: %s", topup_id)
                for payment_id in payment_ids:
                    try:
                        await _notify_delivery(bot, await verify_order(payment_id))
                    except Exception:
                        logger.exception("USDT order verification failed: %s", payment_id)
        except (VerificationError, OSError, TimeoutError, KeyError, ValueError):
            logger.exception("USDT automatic verification iteration failed")
        except Exception:
            logger.exception("unexpected USDT verification loop failure")
        await asyncio.sleep(max(10, settings.usdt_verify_poll_seconds))
