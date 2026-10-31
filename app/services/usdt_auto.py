from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import BalanceTopup, Order, PluginPayment, User
from app.db.session import SessionFactory
from app.plugins.registry import get_plugin_registry
from app.services.plugin_payments import PluginDelivery, fulfill_plugin_payment
from app.services.topups import review_topup
from app.services.usdt_claims import (
    TransactionAlreadyClaimed,
    reserve_transaction_claim,
    update_transaction_claim,
)
from app.services.usdt_verification import (
    VerificationError,
    automatic_verification_available,
    automatic_verification_configured,
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
        destination = topup.destination
        initiated_at = topup.initiated_at or topup.created_at
    if not automatic_verification_available(settings, method):
        return AutoVerificationOutcome("unsupported", reason="未配置自动核验接口")
    if not destination:
        return AutoVerificationOutcome("manual_review", reason="旧充值记录没有收款地址快照")
    try:
        async with SessionFactory() as session, session.begin():
            await reserve_transaction_claim(
                session,
                network=method,
                transaction_id=proof,
                source_type="topup",
                source_id=topup_id,
                destination=destination,
                amount_minor=amount,
            )
    except TransactionAlreadyClaimed as exc:
        return AutoVerificationOutcome("manual_review", reason=str(exc))
    result = await verify_usdt_payment(
        settings,
        method_id=method,
        proof=proof,
        destination=destination,
        expected_amount_minor=amount,
        initiated_at=initiated_at,
    )
    if result.state in {"rejected", "manual_review"}:
        async with SessionFactory() as session, session.begin():
            await update_transaction_claim(
                session,
                source_type="topup",
                source_id=topup_id,
                status="manual_review",
                evidence={"reason": result.reason, "confirmations": result.confirmations},
            )
        return AutoVerificationOutcome("manual_review", reason=result.reason)
    if result.state != "confirmed":
        return AutoVerificationOutcome(result.state, reason=result.reason)
    async with SessionFactory() as session:
        record, telegram_user_id, duplicate = await review_topup(
            session,
            topup_id=topup_id,
            admin_telegram_id=0,
            approve=True,
        )
    async with SessionFactory() as session, session.begin():
        await update_transaction_claim(
            session,
            source_type="topup",
            source_id=topup_id,
            status="consumed",
            evidence={"confirmations": result.confirmations, "amount_minor": result.amount_minor},
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
        initiated_raw = payment.provider_data.get("initiated_at")
        initiated_at = (
            datetime.fromisoformat(str(initiated_raw)) if initiated_raw else payment.created_at
        )
    if not automatic_verification_available(settings, method):
        return AutoVerificationOutcome("unsupported", reference=public_id)
    try:
        async with SessionFactory() as session, session.begin():
            await reserve_transaction_claim(
                session,
                network=method,
                transaction_id=proof,
                source_type="order",
                source_id=payment_id,
                destination=destination,
                amount_minor=amount,
            )
    except TransactionAlreadyClaimed as exc:
        return AutoVerificationOutcome("manual_review", reference=public_id, reason=str(exc))
    result = await verify_usdt_payment(
        settings,
        method_id=method,
        proof=proof,
        destination=destination,
        expected_amount_minor=amount,
        initiated_at=initiated_at,
    )
    if result.state in {"rejected", "manual_review"}:
        async with SessionFactory() as session, session.begin():
            await update_transaction_claim(
                session,
                source_type="order",
                source_id=payment_id,
                status="manual_review",
                evidence={"reason": result.reason, "confirmations": result.confirmations},
            )
        return AutoVerificationOutcome("manual_review", reference=public_id, reason=result.reason)
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
    async with SessionFactory() as session, session.begin():
        await update_transaction_claim(
            session,
            source_type="order",
            source_id=payment_id,
            status="consumed",
            evidence={"confirmations": result.confirmations, "amount_minor": result.amount_minor},
        )
    return AutoVerificationOutcome("paid", delivery=delivery, reference=public_id)


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
                    auto_methods = [
                        method_id
                        for method_id in ("usdt_bep20", "usdt_trc20", "usdt_arbitrum")
                        if automatic_verification_configured(settings, method_id)
                    ]
                    topup_ids = list(
                        await session.scalars(
                            select(BalanceTopup.id)
                            .where(
                                BalanceTopup.status == "pending",
                                BalanceTopup.method.in_(auto_methods),
                            )
                            .order_by(BalanceTopup.created_at.asc())
                            .limit(50)
                        )
                    )
                    payment_ids = list(
                        await session.scalars(
                            select(PluginPayment.id)
                            .where(
                                PluginPayment.status == "proof_pending",
                                PluginPayment.provider.in_(auto_methods),
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
