import asyncio
import html
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiohttp import web
from sqlalchemy import select

from app.db.base import ProductStatus
from app.db.models import SKU, Product, ProductPrice, SupplierOrder, SupplierProduct
from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.audit import record_admin_action
from app.services.supplier_fulfillment import (
    SupplierFulfillmentError,
    fulfill_supplier_order,
    get_supplier_purchase_context,
)
from app.services.supplier_orders import (
    get_or_create_supplier_order,
    get_supplier_order_summary,
    list_supplier_exceptions,
    record_supplier_recovery,
    save_supplier_order_result,
)
from plugins.prodseller.client import ProdsellerClient, ProdsellerError
from plugins.prodseller.config import get_prodseller_settings

logger = logging.getLogger(__name__)
PROVIDER = "prodseller"


class ImportProduct(StatesGroup):
    external_id = State()


class SupplierRecovery(StatesGroup):
    note = State()


class CustomPrice(StatesGroup):
    amount = State()


class ProdsellerPlugin:
    name = PROVIDER

    def __init__(self) -> None:
        self.settings = get_prodseller_settings()
        self.client = ProdsellerClient(self.settings)
        self.router = Router(name="plugin_prodseller")
        self.router.callback_query.register(self._balance, F.data == "plugin:prodseller:balance")
        self.router.callback_query.register(self._sync, F.data == "plugin:prodseller:sync")
        self.router.callback_query.register(
            self._health, F.data == "plugin:prodseller:health"
        )
        self.router.callback_query.register(
            self._browse, F.data.startswith("plugin:prodseller:browse:")
        )
        self.router.callback_query.register(
            self._import_from_browser, F.data.startswith("plugin:prodseller:add:")
        )
        self.router.callback_query.register(
            self._exceptions, F.data.startswith("plugin:prodseller:exceptions:")
        )
        self.router.callback_query.register(
            self._audit_detail, F.data.startswith("plugin:prodseller:audit:")
        )
        self.router.callback_query.register(
            self._apply_suggested_price, F.data.startswith("plugin:prodseller:suggest:")
        )
        self.router.callback_query.register(
            self._mapping_detail, F.data.startswith("plugin:prodseller:mapping:")
        )
        self.router.callback_query.register(
            self._sync_mapping, F.data.startswith("plugin:prodseller:syncmapping:")
        )
        self.router.callback_query.register(
            self._toggle_mapping, F.data.startswith("plugin:prodseller:togglemapping:")
        )
        self.router.callback_query.register(
            self._start_custom_price, F.data.startswith("plugin:prodseller:custom:")
        )
        self.router.callback_query.register(
            self._recover_query, F.data.startswith("plugin:prodseller:recover:")
        )
        self.router.callback_query.register(
            self._recovery_candidates,
            F.data.startswith("plugin:prodseller:candidates:"),
        )
        self.router.callback_query.register(
            self._select_recovery_candidate,
            F.data.startswith("plugin:prodseller:selectcandidate:"),
        )
        self.router.message.register(self._save_recovery_note, SupplierRecovery.note)
        self.router.message.register(self._save_custom_price, CustomPrice.amount)
        self._bot: Bot | None = None
        self._poll_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return InlineKeyboardButton(
            text="🔌 ProdSeller 映射",
            callback_data=f"plugin:prodseller:mapping:{product_id}",
        )

    def admin_home_buttons(self) -> list[list[InlineKeyboardButton]]:
        return [
            [
                InlineKeyboardButton(
                    text="🛍 ProdSeller 商品", callback_data="plugin:prodseller:browse:0"
                ),
                InlineKeyboardButton(
                    text="🔄 同步上游", callback_data="plugin:prodseller:sync"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ 供货异常", callback_data="plugin:prodseller:exceptions:all:0"
                ),
                InlineKeyboardButton(
                    text="💰 ProdSeller 余额", callback_data="plugin:prodseller:balance"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🩺 ProdSeller 健康", callback_data="plugin:prodseller:health"
                )
            ],
        ]

    async def startup(self, bot: Bot) -> None:
        self._bot = bot
        self._poll_task = asyncio.create_task(self._poll_pending_orders())
        self._sync_task = asyncio.create_task(self._sync_products_loop())

    async def shutdown(self) -> None:
        tasks = [task for task in (self._poll_task, self._sync_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def register_http_routes(self, app: web.Application, bot: Bot) -> None:
        return None

    async def fulfill_paid_order(self, bot: Bot, delivered) -> bool:
        if not delivered.needs_supplier or delivered.order_id is None:
            return False
        async with SessionFactory() as session:
            context = await get_supplier_purchase_context(
                session, order_id=delivered.order_id, provider=PROVIDER
            )
        if context is None:
            return False
        async with SessionFactory() as session:
            supplier_order = await get_or_create_supplier_order(
                session,
                order_id=context.order_id,
                provider=PROVIDER,
                cost_currency=context.currency,
            )
        try:
            if supplier_order.external_order_id:
                response = await self.client.get_order(supplier_order.external_order_id)
            else:
                remote = await self.client.get_product(context.external_product_id)
                current_cost_minor = self._minor(remote["price"]) * context.quantity
                if current_cost_minor > context.sale_amount_minor:
                    raise SupplierFulfillmentError("实时上游总成本高于本订单销售金额")
                stock = remote.get("stock")
                if stock is not None and stock < context.quantity:
                    raise SupplierFulfillmentError("上游实时库存不足")
                if remote["requiresEmailActivation"]:
                    raise SupplierFulfillmentError("该上游商品需要客户邮箱，当前订单未收集邮箱")
                response = await self.client.create_order(
                    external_product_id=context.external_product_id,
                    idempotency_key=f"tgfakabot-{context.public_id}",
                    quantity=context.quantity,
                )
            amount_minor = self._minor(response["amount"])
            external_order_id = response["orderId"]
            status = response["status"]
            await self._save_result(
                context.order_id, external_order_id, status, amount_minor, response
            )
            if status != "delivered":
                await bot.send_message(
                    context.telegram_user_id,
                    f"⏳ 订单 <code>{context.public_id}</code> 已提交上游，等待交付。",
                )
                return True
            async with SessionFactory() as session:
                delivery = await fulfill_supplier_order(
                    session,
                    order_id=context.order_id,
                    provider=PROVIDER,
                    external_order_id=external_order_id,
                    delivered_secrets=self._delivered_secrets(response),
                    supplier_amount_minor=amount_minor,
                    supplier_response=response,
                    sale_currency=context.sale_currency,
                    sale_amount_minor=context.sale_amount_minor,
                )
            if delivery.should_deliver:
                await bot.send_message(
                    delivery.telegram_user_id,
                    "✅ <b>支付成功，上游商品已交付</b>\n\n"
                    f"商品：{html.escape(delivery.product_name)}\n"
                    f"订单：<code>{delivery.public_id}</code>\n"
                    f"卡密：<code>{html.escape(delivery.secret)}</code>",
                )
        except (ProdsellerError, SupplierFulfillmentError) as exc:
            logger.exception("ProdSeller fulfillment failed for %s", context.public_id)
            status = self._failure_status(exc)
            await self._save_result(
                context.order_id,
                supplier_order.external_order_id,
                status,
                None,
                supplier_order.response_data,
                str(exc),
            )
            await bot.send_message(
                context.telegram_user_id,
                f"⚠️ 订单 <code>{context.public_id}</code> 已付款，上游供货处理中，请联系客服。",
            )
        return True

    async def _sync_products_loop(self) -> None:
        while True:
            delay = max(1, self.settings.prodseller_product_sync_minutes) * 60
            try:
                if self._rate_limit_exhausted():
                    delay = self._rate_limit_delay(delay)
                else:
                    products = await self.client.list_products()
                    updated, suspended = await self._sync_existing(products)
                    logger.info("ProdSeller automatic product sync updated %s mappings", updated)
                    await self._notify_profit_suspensions(suspended)
            except ProdsellerError as exc:
                logger.exception("ProdSeller automatic product sync failed")
                if exc.status == 429:
                    delay = self._rate_limit_delay(delay)
            except Exception:
                logger.exception("ProdSeller automatic product sync failed")
            await asyncio.sleep(delay)

    async def _poll_pending_orders(self) -> None:
        while True:
            delay = max(10, self.settings.prodseller_order_poll_seconds)
            try:
                await self._poll_pending_once()
            except ProdsellerError as exc:
                logger.exception("ProdSeller pending-order poll failed")
                if exc.status == 429 and exc.rate_limit.retry_after:
                    delay = max(delay, exc.rate_limit.retry_after)
            except Exception:
                logger.exception("ProdSeller pending-order poll failed")
            await asyncio.sleep(delay)

    async def _poll_pending_once(self) -> None:
        from app.db.models import SupplierOrder

        await self._reconcile_missing_external_ids()
        async with SessionFactory() as session:
            pending = (
                await session.scalars(
                    select(SupplierOrder).where(
                        SupplierOrder.provider == PROVIDER,
                        SupplierOrder.status.in_(["pending", "paid", "unknown"]),
                        SupplierOrder.external_order_id.is_not(None),
                    ).limit(50)
                )
            ).all()
        for supplier_order in pending:
            try:
                response = await self.client.get_order(supplier_order.external_order_id)
                amount_minor = self._minor(response["amount"])
                await self._save_result(
                    supplier_order.order_id,
                    response["orderId"],
                    response["status"],
                    amount_minor,
                    response,
                )
                if response["status"] != "delivered":
                    continue
                context = None
                async with SessionFactory() as session:
                    context = await get_supplier_purchase_context(
                        session, order_id=supplier_order.order_id, provider=PROVIDER
                    )
                if context is None:
                    continue
                async with SessionFactory() as session:
                    delivery = await fulfill_supplier_order(
                        session,
                        order_id=context.order_id,
                        provider=PROVIDER,
                        external_order_id=response["orderId"],
                        delivered_secrets=self._delivered_secrets(response),
                        supplier_amount_minor=amount_minor,
                        supplier_response=response,
                        sale_currency=context.sale_currency,
                        sale_amount_minor=context.sale_amount_minor,
                    )
                if delivery.should_deliver and self._bot is not None:
                    await self._bot.send_message(
                        delivery.telegram_user_id,
                        "✅ <b>上游商品已交付</b>\n\n"
                        f"商品：{html.escape(delivery.product_name)}\n"
                        f"订单：<code>{delivery.public_id}</code>\n"
                        f"卡密：<code>{html.escape(delivery.secret)}</code>",
                    )
            except (ProdsellerError, SupplierFulfillmentError):
                logger.exception(
                    "ProdSeller poll failed for supplier order %s", supplier_order.id
                )

    async def _reconcile_missing_external_ids(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        async with SessionFactory() as session:
            missing = (
                await session.scalars(
                    select(SupplierOrder).where(
                        SupplierOrder.provider == PROVIDER,
                        SupplierOrder.status == "unknown",
                        SupplierOrder.external_order_id.is_(None),
                        SupplierOrder.created_at >= cutoff,
                    ).limit(20)
                )
            ).all()
        if not missing or self._rate_limit_exhausted():
            return
        history = await self.client.list_orders(page=1, limit=200)
        upstream_orders = history["orders"]
        for supplier_order in missing:
            async with SessionFactory() as session:
                context = await get_supplier_purchase_context(
                    session, order_id=supplier_order.order_id, provider=PROVIDER
                )
            if context is None:
                continue
            candidates = self._reconciliation_candidates(
                context=context,
                supplier_created_at=supplier_order.created_at,
                orders=upstream_orders,
            )
            if len(candidates) != 1:
                if len(candidates) > 1:
                    await self._save_result(
                        context.order_id, None, "unknown", None, {},
                        "上游订单对账发现多个候选，需管理员人工核验",
                    )
                continue
            response = candidates[0]
            amount_minor = self._minor(response["amount"])
            await self._save_result(
                context.order_id, response["orderId"], response["status"],
                amount_minor, response,
            )
            if response["status"] == "delivered":
                await self._deliver_recovered_order(context.order_id, response)

    @classmethod
    def _reconciliation_candidates(
        cls, *, context, supplier_created_at: datetime, orders: list[dict]
    ) -> list[dict]:
        earliest = supplier_created_at - timedelta(minutes=5)
        latest = supplier_created_at + timedelta(hours=2)
        candidates = []
        for order in orders:
            product = order.get("product")
            created_at = cls._parse_upstream_datetime(order.get("createdAt"))
            if not isinstance(product, dict) or created_at is None:
                continue
            if product.get("id") != context.external_product_id:
                continue
            if order.get("quantity", 1) != context.quantity:
                continue
            amount_minor = cls._minor(order["amount"])
            if amount_minor > context.sale_amount_minor:
                continue
            if not earliest <= created_at <= latest:
                continue
            candidates.append(order)
        return candidates

    @staticmethod
    def _parse_upstream_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    async def _browse(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            page = max(0, int(callback.data.rsplit(":", 1)[1]))
        except (ValueError, IndexError):
            page = 0
        await callback.answer("加载中…")
        try:
            products = await self.client.list_products()
        except ProdsellerError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        products = [item for item in products if not item["requiresEmailActivation"]]
        page_size = 5
        page_items = products[page * page_size : (page + 1) * page_size]
        if not page_items:
            await callback.message.answer("暂无更多可导入的即时交付商品。")
            return
        lines = [f"🛍 <b>ProdSeller 商品 · 第 {page + 1} 页</b>"]
        rows = []
        for item in page_items:
            cost_minor = self._minor(item["price"])
            sale_minor = self._sale_price(cost_minor)
            stock = "有货" if item["inStock"] else "缺货"
            lines.append(
                f"\n<b>{html.escape(item['name'])}</b>\n"
                f"成本 ${cost_minor / 100:.2f} · 建议售价 ${sale_minor / 100:.2f} · {stock}"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"导入 {item['name'][:20]}",
                        callback_data=f"plugin:prodseller:add:{item['id']}",
                    )
                ]
            )
        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ 上一页", callback_data=f"plugin:prodseller:browse:{page - 1}"
                )
            )
        if (page + 1) * page_size < len(products):
            navigation.append(
                InlineKeyboardButton(
                    text="下一页 ➡️", callback_data=f"plugin:prodseller:browse:{page + 1}"
                )
            )
        if navigation:
            rows.append(navigation)
        from aiogram.types import InlineKeyboardMarkup

        await callback.message.answer(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def _import_from_browser(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        external_id = callback.data.rsplit(":", 1)[1]
        await callback.answer("导入中…")
        try:
            products = await self.client.list_products()
            product = next(item for item in products if item["id"] == external_id)
            if product["requiresEmailActivation"]:
                raise ProdsellerError("该商品需要买家邮箱，当前版本暂不支持")
            local_id = await self._import_product(
                product, admin_telegram_id=callback.from_user.id
            )
        except StopIteration:
            await callback.message.answer("❌ 上游商品不存在。")
            return
        except ProdsellerError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        await callback.message.answer(
            "✅ 商品已导入为草稿；建议售价默认关闭。\n"
            f"本地商品 ID：<code>{local_id}</code>"
        )

    async def _exceptions(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            _, _, _, raw_status, raw_page = callback.data.split(":", 4)
            status = None if raw_status == "all" else raw_status
            page = max(0, int(raw_page))
        except (ValueError, IndexError):
            status, page = None, 0
        await callback.answer()
        page_size = 5
        async with SessionFactory() as session:
            exceptions = await list_supplier_exceptions(
                session,
                provider=PROVIDER,
                status=status,
                offset=page * page_size,
                limit=page_size + 1,
            )
        has_next = len(exceptions) > page_size
        exceptions = exceptions[:page_size]
        if not exceptions:
            await callback.message.answer("✅ 当前筛选条件下没有待处理供货订单。")
            return
        label = status or "全部"
        lines = [f"⚠️ <b>ProdSeller 供货异常 · {label} · 第 {page + 1} 页</b>"]
        rows = [
            [
                InlineKeyboardButton(
                    text="全部", callback_data="plugin:prodseller:exceptions:all:0"
                ),
                InlineKeyboardButton(
                    text="失败", callback_data="plugin:prodseller:exceptions:failed:0"
                ),
                InlineKeyboardButton(
                    text="未知", callback_data="plugin:prodseller:exceptions:unknown:0"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="待处理", callback_data="plugin:prodseller:exceptions:pending:0"
                ),
                InlineKeyboardButton(
                    text="已付款", callback_data="plugin:prodseller:exceptions:paid:0"
                ),
            ],
        ]
        for item in exceptions:
            error = html.escape(item.last_error or "暂无错误")
            lines.append(
                f"\n<b>{html.escape(item.product_name)}</b>\n"
                f"<code>{item.public_id}</code> · {item.status}\n{error}"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"📋 详情 {item.public_id}",
                        callback_data=f"plugin:prodseller:audit:{item.id}",
                    ),
                    InlineKeyboardButton(
                        text="🔎 查询恢复",
                        callback_data=f"plugin:prodseller:recover:{item.id}",
                    ),
                    InlineKeyboardButton(
                        text="🧩 候选订单",
                        callback_data=f"plugin:prodseller:candidates:{item.id}",
                    ),
                ]
            )
        navigation = []
        filter_value = status or "all"
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ 上一页",
                    callback_data=f"plugin:prodseller:exceptions:{filter_value}:{page - 1}",
                )
            )
        if has_next:
            navigation.append(
                InlineKeyboardButton(
                    text="下一页 ➡️",
                    callback_data=f"plugin:prodseller:exceptions:{filter_value}:{page + 1}",
                )
            )
        if navigation:
            rows.append(navigation)
        from aiogram.types import InlineKeyboardMarkup

        await callback.message.answer(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )

    async def _audit_detail(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            supplier_order_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await callback.answer()
        async with SessionFactory() as session:
            summary = await get_supplier_order_summary(
                session, supplier_order_id=supplier_order_id
            )
            supplier_order = await session.get(SupplierOrder, supplier_order_id)
        if summary is None or supplier_order is None:
            await callback.message.answer("订单不存在。")
            return
        response_keys = ", ".join(sorted(supplier_order.response_data.keys())) or "无"
        cost = (
            f"{summary.cost_amount_minor / 100:.2f} {summary.cost_currency}"
            if summary.cost_amount_minor is not None
            else "未知"
        )
        await callback.message.answer(
            "📋 <b>供货订单审计</b>\n\n"
            f"商品：{html.escape(summary.product_name)}\n"
            f"本地订单：<code>{summary.public_id}</code>\n"
            f"上游订单：<code>{html.escape(summary.external_order_id or '无')}</code>\n"
            f"状态：{html.escape(summary.status)}\n"
            f"成本：{cost}\n"
            f"错误：{html.escape(summary.last_error or '无')}\n"
            f"恢复人：<code>{supplier_order.recovered_by_telegram_id or '无'}</code>\n"
            f"恢复时间：{supplier_order.recovered_at or '无'}\n"
            f"恢复说明：{html.escape(supplier_order.recovery_note or '无')}\n"
            f"响应字段：{html.escape(response_keys)}"
        )

    async def _recovery_candidates(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            supplier_order_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            supplier_order = await session.get(SupplierOrder, supplier_order_id)
            context = (
                await get_supplier_purchase_context(
                    session, order_id=supplier_order.order_id, provider=PROVIDER
                )
                if supplier_order
                else None
            )
        if supplier_order is None or context is None:
            await callback.answer("本地供货订单不存在", show_alert=True)
            return
        if supplier_order.external_order_id:
            await callback.answer("该订单已有上游订单号", show_alert=True)
            return
        history = await self.client.list_orders(page=1, limit=200)
        candidates = self._reconciliation_candidates(
            context=context,
            supplier_created_at=supplier_order.created_at,
            orders=history["orders"],
        )
        if not candidates:
            await callback.answer("没有符合商品、数量、金额和时间的候选", show_alert=True)
            return
        from aiogram.types import InlineKeyboardMarkup

        lines = ["🧩 <b>上游订单候选</b>", f"本地订单：<code>{context.public_id}</code>"]
        rows = []
        for order in candidates[:10]:
            created = html.escape(str(order.get("createdAt") or "未知"))
            lines.append(
                f"\n<code>{html.escape(order['orderId'])}</code> · {order['status']}\n"
                f"数量 {order['quantity']} · ${order['amount']:.2f} · {created}"
            )
            rows.append(
                [InlineKeyboardButton(
                    text=f"选择 {order['orderId'][:12]}",
                    callback_data=(
                        f"plugin:prodseller:selectcandidate:{supplier_order_id}:"
                        f"{order['orderId']}"
                    ),
                    style="danger",
                )]
            )
        await callback.answer()
        await callback.message.answer(
            "\n".join(lines) + "\n\n⚠️ 仅在人工核对确认后选择。",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _select_recovery_candidate(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            prefix = "plugin:prodseller:selectcandidate:"
            supplier_raw, external_order_id = callback.data[len(prefix):].split(":", 1)
            supplier_order_id = uuid.UUID(supplier_raw)
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        response = await self.client.get_order(external_order_id)
        async with SessionFactory() as session:
            supplier_order = await record_supplier_recovery(
                session,
                supplier_order_id=supplier_order_id,
                admin_telegram_id=callback.from_user.id,
                note=f"管理员从候选订单中确认上游订单 {external_order_id}",
            )
            context = await get_supplier_purchase_context(
                session, order_id=supplier_order.order_id, provider=PROVIDER
            )
        if context is None:
            await callback.answer("找不到本地采购上下文", show_alert=True)
            return
        candidates = self._reconciliation_candidates(
            context=context,
            supplier_created_at=supplier_order.created_at,
            orders=[response],
        )
        if len(candidates) != 1:
            await callback.answer("该上游订单不再符合安全匹配条件", show_alert=True)
            return
        await self._save_result(
            context.order_id, response["orderId"], response["status"],
            self._minor(response["amount"]), response,
        )
        if response["status"] == "delivered":
            await self._deliver_recovered_order(context.order_id, response)
        await callback.answer("候选已绑定", show_alert=True)
        await callback.message.answer(
            f"✅ 已绑定上游订单 <code>{html.escape(response['orderId'])}</code>，"
            f"状态：{html.escape(response['status'])}"
        )

    async def _recover_query(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            supplier_order_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await state.set_state(SupplierRecovery.note)
        await state.update_data(supplier_order_id=str(supplier_order_id))
        await callback.answer()
        await callback.message.answer(
            "请输入本次上游核验说明（至少 6 字符）。提交后会查询上游并尝试安全恢复交付。"
        )

    async def _save_recovery_note(self, message: Message, state: FSMContext) -> None:
        if not self._is_admin(message.from_user.id):
            return
        note = (message.text or "").strip()
        data = await state.get_data()
        try:
            supplier_order_id = uuid.UUID(data["supplier_order_id"])
            async with SessionFactory() as session:
                supplier_order = await record_supplier_recovery(
                    session,
                    supplier_order_id=supplier_order_id,
                    admin_telegram_id=message.from_user.id,
                    note=note,
                )
            if not supplier_order.external_order_id:
                raise ValueError("该记录尚无上游订单号，不能直接查询；请联系客服核验采购结果")
            response = await self.client.get_order(supplier_order.external_order_id)
            amount_minor = self._minor(response["amount"])
            await self._save_result(
                supplier_order.order_id,
                response["orderId"],
                response["status"],
                amount_minor,
                response,
            )
            if response["status"] == "delivered":
                await self._deliver_recovered_order(supplier_order.order_id, response)
        except (KeyError, ValueError, ProdsellerError, SupplierFulfillmentError) as exc:
            await message.answer(f"❌ {html.escape(str(exc))}")
            return
        await state.clear()
        await message.answer(f"✅ 上游状态已刷新：{html.escape(response['status'])}")

    async def _deliver_recovered_order(self, order_id: uuid.UUID, response: dict) -> None:
        async with SessionFactory() as session:
            context = await get_supplier_purchase_context(
                session, order_id=order_id, provider=PROVIDER
            )
        if context is None:
            raise SupplierFulfillmentError("找不到订单的上游商品映射")
        async with SessionFactory() as session:
            delivery = await fulfill_supplier_order(
                session,
                order_id=context.order_id,
                provider=PROVIDER,
                external_order_id=response["orderId"],
                delivered_secrets=self._delivered_secrets(response),
                supplier_amount_minor=self._minor(response["amount"]),
                supplier_response=response,
                sale_currency=context.sale_currency,
                sale_amount_minor=context.sale_amount_minor,
            )
        if delivery.should_deliver and self._bot is not None:
            await self._bot.send_message(
                delivery.telegram_user_id,
                "✅ <b>上游订单已恢复并完成交付</b>\n\n"
                f"订单：<code>{delivery.public_id}</code>\n"
                f"卡密：<code>{html.escape(delivery.secret)}</code>",
            )

    async def _mapping_detail(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            row = (
                await session.execute(
                    select(Product, SKU, SupplierProduct, ProductPrice)
                    .join(SKU, SKU.product_id == Product.id)
                    .join(SupplierProduct, SupplierProduct.sku_id == SKU.id)
                    .outerjoin(
                        ProductPrice,
                        (ProductPrice.sku_id == SKU.id)
                        & (ProductPrice.provider == "bepusdt"),
                    )
                    .where(
                        Product.id == product_id,
                        SupplierProduct.provider == PROVIDER,
                    )
                )
            ).first()
        if row is None:
            await callback.answer("该商品没有 ProdSeller 映射", show_alert=True)
            return
        product, _, mapping, price = row
        suggested = self._sale_price(mapping.cost_amount_minor)
        actual = price.amount_minor if price else None
        profit = actual - mapping.cost_amount_minor if actual is not None else None
        from aiogram.types import InlineKeyboardMarkup

        await callback.answer()
        await callback.message.answer(
            "🔌 <b>ProdSeller 商品映射</b>\n\n"
            f"商品：{html.escape(product.name)}\n"
            f"上游 ID：<code>{html.escape(mapping.external_product_id)}</code>\n"
            f"上游成本：${mapping.cost_amount_minor / 100:.2f}\n"
            f"建议售价：${suggested / 100:.2f}\n"
            f"实际售价：{f'${actual / 100:.2f}' if actual is not None else '未配置'}\n"
            f"单件毛利：{f'${profit / 100:.2f}' if profit is not None else '未知'}\n"
            f"上游库存：{'有货' if mapping.in_stock else '缺货'}\n"
            f"映射状态：{'暂停' if mapping.suspended_for_profit else '正常'}\n"
            f"最近同步：{mapping.last_synced_at or '尚未同步'}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔄 立即同步",
                        callback_data=f"plugin:prodseller:syncmapping:{mapping.id}",
                    ),
                    InlineKeyboardButton(
                        text="⏯ 切换映射",
                        callback_data=f"plugin:prodseller:togglemapping:{mapping.id}",
                        style="danger",
                    ),
                ]]
            ),
        )

    async def _sync_mapping(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            mapping_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session:
            mapping = await session.get(SupplierProduct, mapping_id)
        if mapping is None or mapping.provider != PROVIDER:
            await callback.answer("映射不存在", show_alert=True)
            return
        remote = await self.client.get_product(mapping.external_product_id)
        _, suspended = await self._sync_existing([remote])
        await callback.answer("同步完成", show_alert=True)
        await callback.message.answer(
            f"✅ 单商品同步完成；利润保护停用 {len(suspended)} 个。"
        )

    async def _toggle_mapping(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            mapping_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session, session.begin():
            mapping = await session.scalar(
                select(SupplierProduct)
                .where(SupplierProduct.id == mapping_id, SupplierProduct.provider == PROVIDER)
                .with_for_update()
            )
            if mapping is None:
                await callback.answer("映射不存在", show_alert=True)
                return
            mapping.suspended_for_profit = not mapping.suspended_for_profit
            mapping.suspension_reason = (
                "manually suspended by administrator"
                if mapping.suspended_for_profit
                else None
            )
            await record_admin_action(
                session,
                admin_telegram_id=callback.from_user.id,
                action="supplier_mapping_toggle",
                target_type="supplier_product",
                target_id=str(mapping.id),
                details={
                    "provider": PROVIDER,
                    "suspended": mapping.suspended_for_profit,
                },
            )
            suspended = mapping.suspended_for_profit
        await callback.answer("映射已暂停" if suspended else "映射已恢复", show_alert=True)

    async def _apply_suggested_price(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            supplier_product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        async with SessionFactory() as session, session.begin():
            mapping = await session.scalar(
                select(SupplierProduct)
                .where(SupplierProduct.id == supplier_product_id)
                .with_for_update()
            )
            if mapping is None:
                await callback.answer("上游商品不存在", show_alert=True)
                return
            price = await session.scalar(
                select(ProductPrice)
                .where(
                    ProductPrice.sku_id == mapping.sku_id,
                    ProductPrice.provider == "bepusdt",
                )
                .with_for_update()
            )
            if price is None:
                await callback.answer("销售价格不存在", show_alert=True)
                return
            price.amount_minor = self._sale_price(mapping.cost_amount_minor)
            price.is_active = True
            mapping.suspended_for_profit = False
            mapping.suspension_reason = None
        await callback.answer("已应用建议售价")
        await callback.message.answer("✅ 已更新并启用 BEpusdt 建议售价。")

    async def _start_custom_price(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        try:
            supplier_product_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await callback.answer("参数错误", show_alert=True)
            return
        await state.set_state(CustomPrice.amount)
        await state.update_data(supplier_product_id=str(supplier_product_id))
        await callback.answer()
        await callback.message.answer("请输入新的 USD 售价，例如 9.99。")

    async def _save_custom_price(self, message: Message, state: FSMContext) -> None:
        if not self._is_admin(message.from_user.id):
            return
        try:
            amount = Decimal((message.text or "").strip())
            if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                raise ValueError
            amount_minor = int(amount * 100)
            data = await state.get_data()
            supplier_product_id = uuid.UUID(data["supplier_product_id"])
            async with SessionFactory() as session, session.begin():
                mapping = await session.scalar(
                    select(SupplierProduct)
                    .where(SupplierProduct.id == supplier_product_id)
                    .with_for_update()
                )
                if mapping is None:
                    raise ValueError("上游商品不存在")
                required = self._sale_price(mapping.cost_amount_minor)
                if amount_minor < required:
                    raise ValueError(f"售价低于当前最低安全售价 ${required / 100:.2f}")
                price = await session.scalar(
                    select(ProductPrice)
                    .where(
                        ProductPrice.sku_id == mapping.sku_id,
                        ProductPrice.provider == "bepusdt",
                    )
                    .with_for_update()
                )
                if price is None:
                    raise ValueError("销售价格不存在")
                price.amount_minor = amount_minor
                price.is_active = True
                mapping.suspended_for_profit = False
                mapping.suspension_reason = None
        except (ValueError, KeyError) as exc:
            await message.answer(f"❌ {html.escape(str(exc) or '价格无效')}")
            return
        await state.clear()
        await message.answer(f"✅ 已更新并启用售价：${amount_minor / 100:.2f}")

    async def _balance(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer()
        try:
            balance = await self.client.balance()
        except ProdsellerError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        await callback.message.answer(f"ProdSeller 余额：${balance:.2f} USD")

    async def _health(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer("检查中…")
        try:
            balance = await self.client.balance()
            products = await self.client.list_products()
            async with SessionFactory() as session:
                mappings = (
                    await session.scalars(
                        select(SupplierProduct).where(SupplierProduct.provider == PROVIDER)
                    )
                ).all()
            last_sync = max(
                (mapping.last_synced_at for mapping in mappings if mapping.last_synced_at),
                default=None,
            )
            rate = self.client.rate_limit
            quota = (
                f"{rate.remaining}/{rate.limit}"
                if rate.remaining is not None and rate.limit is not None
                else "上游未返回"
            )
            await callback.message.answer(
                "🩺 <b>ProdSeller 健康状态</b>\n\n"
                "API 鉴权：✅ 正常\n"
                f"余额：${balance:.2f} USD\n"
                f"上游商品：{len(products)}\n"
                f"本地映射：{len(mappings)}\n"
                f"最近同步：{last_sync or '尚未同步'}\n"
                f"请求额度：{quota}\n"
                f"后台保留：{self.settings.prodseller_rate_limit_reserve}"
            )
        except ProdsellerError as exc:
            await callback.message.answer(
                "🩺 <b>ProdSeller 健康状态</b>\n\n"
                f"API：❌ {html.escape(str(exc))}\n"
                f"HTTP：{exc.status or '网络/未知'}"
            )

    async def _sync(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer("同步中…")
        try:
            products = await self.client.list_products()
            updated, suspended = await self._sync_existing(products)
            await self._notify_profit_suspensions(suspended)
            async with SessionFactory() as session, session.begin():
                await record_admin_action(
                    session,
                    admin_telegram_id=callback.from_user.id,
                    action="supplier_manual_sync",
                    target_type="supplier",
                    target_id=PROVIDER,
                    details={
                        "upstream_products": len(products),
                        "updated_mappings": updated,
                        "profit_suspended": len(suspended),
                    },
                )
        except ProdsellerError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        await callback.message.answer(
            f"✅ 已同步 {updated} 个已接入商品；利润保护停用 {len(suspended)} 个。"
        )

    async def _start_import(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await state.set_state(ImportProduct.external_id)
        await callback.answer()
        await callback.message.answer("请输入 ProdSeller 商品 ID；发送 /cancel 取消。")

    async def _finish_import(self, message: Message, state: FSMContext) -> None:
        if not self._is_admin(message.from_user.id):
            return
        external_id = (message.text or "").strip()
        try:
            products = await self.client.list_products()
            product = next(item for item in products if item["id"] == external_id)
        except StopIteration:
            await message.answer("❌ 未找到该上游商品。")
            return
        except ProdsellerError as exc:
            await message.answer(f"❌ {html.escape(str(exc))}")
            return
        if product["requiresEmailActivation"]:
            await message.answer("❌ 该商品需要买家邮箱激活，当前版本暂不导入。")
            return
        local_id = await self._import_product(
            product, admin_telegram_id=message.from_user.id
        )
        await state.clear()
        await message.answer(
            "✅ 已导入上游商品。\n"
            f"本地商品 ID：<code>{local_id}</code>\n"
            "请在商品列表设置 BEpusdt 美元售价后再上架销售。"
        )

    @staticmethod
    def _json_safe_remote(remote: dict) -> dict:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in remote.items()
        }

    async def _import_product(
        self, remote: dict, *, admin_telegram_id: int | None = None
    ) -> uuid.UUID:
        cost_minor = self._minor(remote["price"])
        sale_minor = self._sale_price(cost_minor)
        async with SessionFactory() as session, session.begin():
            supplier = await session.scalar(
                select(SupplierProduct).where(
                    SupplierProduct.provider == PROVIDER,
                    SupplierProduct.external_product_id == remote["id"],
                )
            )
            if supplier:
                sku = await session.get(SKU, supplier.sku_id)
                return sku.product_id
            product = Product(
                name=remote["name"][:200],
                description=str(remote.get("description") or "")[:3000],
                status=ProductStatus.DRAFT,
            )
            session.add(product)
            await session.flush()
            sku = SKU(
                product_id=product.id,
                code=f"prodseller-{remote['id']}",
                name="Default",
                price_xtr=1,
                stars_enabled=False,
                is_default=True,
                is_active=True,
            )
            session.add(sku)
            await session.flush()
            session.add(
                SupplierProduct(
                    sku_id=sku.id,
                    provider=PROVIDER,
                    external_product_id=remote["id"],
                    cost_currency="USD",
                    cost_amount_minor=cost_minor,
                    in_stock=remote["inStock"],
                    requires_customer_email=remote["requiresEmailActivation"],
                    metadata_json=self._json_safe_remote(remote),
                )
            )
            session.add(
                ProductPrice(
                    sku_id=sku.id,
                    provider="bepusdt",
                    currency="USD",
                    amount_minor=sale_minor,
                    is_active=False,
                    metadata_json={"source": PROVIDER},
                )
            )
            if admin_telegram_id is not None:
                await record_admin_action(
                    session,
                    admin_telegram_id=admin_telegram_id,
                    action="supplier_product_import",
                    target_type="product",
                    target_id=str(product.id),
                    details={
                        "provider": PROVIDER,
                        "external_product_id": remote["id"],
                        "cost_amount_minor": cost_minor,
                        "suggested_sale_minor": sale_minor,
                    },
                )
            return product.id

    async def _sync_existing(
        self, products: list[dict]
    ) -> tuple[int, list[tuple[uuid.UUID, str, int, int]]]:
        by_id = {product["id"]: product for product in products}
        updated = 0
        suspended: list[tuple[uuid.UUID, str, int, int]] = []
        async with SessionFactory() as session, session.begin():
            mappings = (
                await session.scalars(
                    select(SupplierProduct).where(SupplierProduct.provider == PROVIDER)
                )
            ).all()
            for mapping in mappings:
                remote = by_id.get(mapping.external_product_id)
                if remote is None:
                    mapping.in_stock = False
                    continue
                new_cost = self._minor(remote["price"])
                mapping.cost_amount_minor = new_cost
                mapping.in_stock = remote["inStock"]
                mapping.requires_customer_email = remote["requiresEmailActivation"]
                mapping.metadata_json = self._json_safe_remote(remote)
                mapping.last_synced_at = datetime.now(UTC)
                price = await session.scalar(
                    select(ProductPrice).where(
                        ProductPrice.sku_id == mapping.sku_id,
                        ProductPrice.provider == "bepusdt",
                    )
                )
                required_sale = self._sale_price(new_cost)
                if price and price.is_active and price.amount_minor < required_sale:
                    price.is_active = False
                    mapping.suspended_for_profit = True
                    mapping.suspension_reason = (
                        f"sale {price.amount_minor} below required {required_sale}"
                    )
                    name = str(remote.get("name") or mapping.external_product_id)
                    suspended.append(
                        (mapping.id, name, price.amount_minor, required_sale)
                    )
                elif price and price.amount_minor >= required_sale:
                    mapping.suspended_for_profit = False
                    mapping.suspension_reason = None
                updated += 1
        return updated, suspended

    async def _notify_profit_suspensions(
        self, suspended: list[tuple[uuid.UUID, str, int, int]]
    ) -> None:
        if not suspended or self._bot is None:
            return
        from aiogram.types import InlineKeyboardMarkup

        from app.core.config import get_settings

        for mapping_id, name, old_price, suggested in suspended:
            text = (
                "⚠️ <b>商品因利润不足已停用</b>\n\n"
                f"商品：{html.escape(name)}\n"
                f"原售价：${old_price / 100:.2f}\n"
                f"最低建议售价：${suggested / 100:.2f}"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"✅ 使用 ${suggested / 100:.2f}",
                            callback_data=f"plugin:prodseller:suggest:{mapping_id}",
                        ),
                        InlineKeyboardButton(
                            text="✏️ 自定义售价",
                            callback_data=f"plugin:prodseller:custom:{mapping_id}",
                        ),
                    ]
                ]
            )
            for admin_id in get_settings().admin_ids:
                try:
                    await self._bot.send_message(admin_id, text, reply_markup=keyboard)
                except Exception:
                    logger.exception("failed to notify admin %s about profit suspension", admin_id)

    async def _save_result(
        self,
        order_id,
        external_order_id,
        status,
        amount_minor,
        response,
        error=None,
    ) -> None:
        safe_response = {
            key: value
            for key, value in response.items()
            if key not in {"deliveredKey", "deliveredKeys"}
        }
        async with SessionFactory() as session:
            await save_supplier_order_result(
                session,
                order_id=order_id,
                provider=PROVIDER,
                external_order_id=external_order_id,
                status=status,
                cost_amount_minor=amount_minor,
                response_data=safe_response,
                last_error=error,
            )

    def _rate_limit_exhausted(self) -> bool:
        remaining = self.client.rate_limit.remaining
        return (
            remaining is not None
            and remaining <= self.settings.prodseller_rate_limit_reserve
        )

    def _rate_limit_delay(self, default: int) -> int:
        retry_after = self.client.rate_limit.retry_after
        if retry_after:
            return max(default, retry_after)
        reset_at = self.client.rate_limit.reset_at
        if reset_at:
            reset_delay = reset_at - int(datetime.now(UTC).timestamp()) + 1
            return max(default, reset_delay)
        return default

    @staticmethod
    def _failure_status(exc: Exception) -> str:
        if isinstance(exc, SupplierFulfillmentError):
            return "failed"
        if isinstance(exc, ProdsellerError) and not exc.retryable:
            return "failed"
        return "unknown"

    def _sale_price(self, cost_minor: int) -> int:
        multiplier = (Decimal(100) + self.settings.prodseller_profit_percent) / Decimal(100)
        fixed_minor = self.settings.prodseller_profit_fixed * Decimal(100)
        sale = Decimal(cost_minor) * multiplier + fixed_minor
        return int(sale.to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def _minor(amount: Decimal) -> int:
        return int(amount * 100)

    @staticmethod
    def _delivered_secrets(order: dict) -> list[str]:
        values = order.get("deliveredKeys")
        if isinstance(values, list) and values and all(
            isinstance(item, str) and item for item in values
        ):
            return values
        raise SupplierFulfillmentError("上游 delivered 订单未包含卡密")

    @staticmethod
    def _is_admin(user_id: int) -> bool:
        from app.core.config import get_settings

        return user_id in get_settings().admin_ids


def create_plugin() -> ProdsellerPlugin:
    return ProdsellerPlugin()
