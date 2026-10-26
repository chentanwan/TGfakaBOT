import asyncio
import html
import logging
import uuid
from decimal import ROUND_CEILING, Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiohttp import web
from sqlalchemy import select

from app.db.base import ProductStatus
from app.db.models import SKU, Product, ProductPrice, SupplierProduct
from app.db.session import SessionFactory
from app.plugins.base import PluginProduct
from app.services.supplier_fulfillment import (
    SupplierFulfillmentError,
    fulfill_supplier_order,
    get_supplier_purchase_context,
)
from app.services.supplier_orders import (
    get_or_create_supplier_order,
    save_supplier_order_result,
)
from plugins.prodseller.client import ProdsellerClient, ProdsellerError
from plugins.prodseller.config import get_prodseller_settings

logger = logging.getLogger(__name__)
PROVIDER = "prodseller"


class ImportProduct(StatesGroup):
    external_id = State()


class ProdsellerPlugin:
    name = PROVIDER

    def __init__(self) -> None:
        self.settings = get_prodseller_settings()
        self.client = ProdsellerClient(self.settings)
        self.router = Router(name="plugin_prodseller")
        self.router.callback_query.register(self._balance, F.data == "plugin:prodseller:balance")
        self.router.callback_query.register(self._sync, F.data == "plugin:prodseller:sync")
        self.router.callback_query.register(
            self._start_import, F.data == "plugin:prodseller:import"
        )
        self.router.message.register(self._finish_import, ImportProduct.external_id)
        self._bot: Bot | None = None
        self._poll_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None

    def catalog_button(self, product: PluginProduct) -> InlineKeyboardButton | None:
        return None

    def admin_product_button(self, product_id: uuid.UUID) -> InlineKeyboardButton | None:
        return None

    def admin_home_buttons(self) -> list[list[InlineKeyboardButton]]:
        return [
            [
                InlineKeyboardButton(
                    text="🚚 导入 ProdSeller 商品", callback_data="plugin:prodseller:import"
                ),
                InlineKeyboardButton(
                    text="🔄 同步上游", callback_data="plugin:prodseller:sync"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💰 ProdSeller 余额", callback_data="plugin:prodseller:balance"
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
                response = await self.client.create_order(
                    external_product_id=context.external_product_id,
                    idempotency_key=f"tgfakabot-{context.public_id}",
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
            secret = self._delivered_secret(response)
            async with SessionFactory() as session:
                delivery = await fulfill_supplier_order(
                    session,
                    order_id=context.order_id,
                    provider=PROVIDER,
                    external_order_id=external_order_id,
                    delivered_secret=secret,
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
            await self._save_result(
                context.order_id,
                supplier_order.external_order_id,
                "unknown",
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
            try:
                products = await self.client.list_products()
                updated = await self._sync_existing(products)
                logger.info("ProdSeller automatic product sync updated %s mappings", updated)
            except Exception:
                logger.exception("ProdSeller automatic product sync failed")
            await asyncio.sleep(
                max(1, self.settings.prodseller_product_sync_minutes) * 60
            )

    async def _poll_pending_orders(self) -> None:
        while True:
            try:
                await self._poll_pending_once()
            except Exception:
                logger.exception("ProdSeller pending-order poll failed")
            await asyncio.sleep(max(10, self.settings.prodseller_order_poll_seconds))

    async def _poll_pending_once(self) -> None:
        from app.db.models import SupplierOrder

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
                        delivered_secret=self._delivered_secret(response),
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

    async def _sync(self, callback: CallbackQuery) -> None:
        if not self._is_admin(callback.from_user.id):
            await callback.answer("无权限", show_alert=True)
            return
        await callback.answer("同步中…")
        try:
            products = await self.client.list_products()
            updated = await self._sync_existing(products)
        except ProdsellerError as exc:
            await callback.message.answer(f"❌ {html.escape(str(exc))}")
            return
        await callback.message.answer(f"✅ 已同步 {updated} 个已接入的上游商品。")

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
        local_id = await self._import_product(product)
        await state.clear()
        await message.answer(
            "✅ 已导入上游商品。\n"
            f"本地商品 ID：<code>{local_id}</code>\n"
            "请在商品列表设置 BEpusdt 美元售价后再上架销售。"
        )

    async def _import_product(self, remote: dict) -> uuid.UUID:
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
                    metadata_json=remote,
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
            return product.id

    async def _sync_existing(self, products: list[dict]) -> int:
        by_id = {product["id"]: product for product in products}
        updated = 0
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
                mapping.cost_amount_minor = self._minor(remote["price"])
                mapping.in_stock = remote["inStock"]
                mapping.requires_customer_email = remote["requiresEmailActivation"]
                mapping.metadata_json = remote
                updated += 1
        return updated

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

    def _sale_price(self, cost_minor: int) -> int:
        multiplier = (Decimal(100) + self.settings.prodseller_profit_percent) / Decimal(100)
        fixed_minor = self.settings.prodseller_profit_fixed * Decimal(100)
        sale = Decimal(cost_minor) * multiplier + fixed_minor
        return int(sale.to_integral_value(rounding=ROUND_CEILING))

    @staticmethod
    def _minor(amount: Decimal) -> int:
        return int(amount * 100)

    @staticmethod
    def _delivered_secret(order: dict) -> str:
        value = order.get("deliveredKey")
        if isinstance(value, str) and value:
            return value
        values = order.get("deliveredKeys")
        if isinstance(values, list) and values and all(isinstance(item, str) for item in values):
            return "\n".join(values)
        raise SupplierFulfillmentError("上游 delivered 订单未包含卡密")

    @staticmethod
    def _is_admin(user_id: int) -> bool:
        from app.core.config import get_settings

        return user_id in get_settings().admin_ids


def create_plugin() -> ProdsellerPlugin:
    return ProdsellerPlugin()
