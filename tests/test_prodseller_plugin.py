from decimal import Decimal
from pathlib import Path

import pytest

from plugins.prodseller.client import ProdsellerClient, ProdsellerError
from plugins.prodseller.config import ProdsellerSettings
from plugins.prodseller.plugin import ProdsellerPlugin


def test_prodseller_key_is_not_committed() -> None:
    tracked_examples = Path(".env.example").read_text() + Path("README.md").read_text()
    assert "psk_" + "ad8428" not in tracked_examples
    assert "PRODSELLER_API_KEY=" in Path(".env.example").read_text()


def test_documented_product_shape_is_accepted() -> None:
    product = ProdsellerClient._validate_product(
        {
            "id": "64abc",
            "name": "Netflix 1 Month",
            "description": "Premium account",
            "price": 4.99,
            "publicPrice": 5.99,
            "delivery": {"type": "instant"},
            "inStock": True,
            "requiresEmailActivation": False,
        }
    )
    assert product["price"] == Decimal("4.99")
    assert product["inStock"] is True


def test_amount_rejects_more_than_two_decimals() -> None:
    with pytest.raises(ProdsellerError):
        ProdsellerClient._amount("4.999")


def test_product_detail_stock_shape_is_normalized() -> None:
    product = ProdsellerClient._validate_product(
        {"id": "p1", "name": "Item", "price": "1.25", "stock": 4}
    )
    assert product["stock"] == 4
    assert product["inStock"] is True


def test_multi_key_order_shape_is_normalized() -> None:
    order = ProdsellerClient._validate_order(
        {
            "orderId": "o1",
            "status": "delivered",
            "quantity": 2,
            "amount": "2.50",
            "deliveredKeys": ["key-1", "key-2"],
        }
    )
    assert order["deliveredKeys"] == ["key-1", "key-2"]
    assert order["quantity"] == 2


def test_single_key_order_is_normalized_to_list() -> None:
    order = ProdsellerClient._validate_order(
        {
            "orderId": "o1",
            "status": "delivered",
            "amount": "1.25",
            "deliveredKey": "key-1",
        }
    )
    assert order["deliveredKeys"] == ["key-1"]


def test_prodseller_error_classifies_retryability() -> None:
    assert ProdsellerError("rate", status=429).retryable is True
    assert ProdsellerError("server", status=500).retryable is True
    assert ProdsellerError("stock", status=409).retryable is False


def test_supplier_plugin_uses_upstream_idempotency_key() -> None:
    source = Path("plugins/prodseller/client.py").read_text()
    assert '"Idempotency-Key"' in source
    plugin = Path("plugins/prodseller/plugin.py").read_text()
    assert 'f"tgfakabot-{context.public_id}"' in plugin


def test_supplier_profit_combines_percentage_and_fixed_amount() -> None:
    plugin = object.__new__(ProdsellerPlugin)
    plugin.settings = ProdsellerSettings(
        prodseller_api_key="test",
        prodseller_profit_percent="20",
        prodseller_profit_fixed="1.50",
    )
    assert plugin._sale_price(500) == 750


def test_product_sync_defaults_to_sixty_minutes() -> None:
    settings = ProdsellerSettings(prodseller_api_key="test")
    assert settings.prodseller_product_sync_minutes == 60
    assert settings.prodseller_order_poll_seconds == 30


def test_every_example_environment_variable_has_comment() -> None:
    lines = Path(".env.example").read_text().splitlines()
    for index, line in enumerate(lines):
        if not line or line.startswith("#"):
            continue
        assert index > 0 and lines[index - 1].startswith("#"), line


def test_admin_has_product_browser_and_exception_recovery() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:browse:" in source
    assert "plugin:prodseller:add:" in source
    assert "plugin:prodseller:exceptions" in source
    assert "record_supplier_recovery" in source


def test_sync_suspends_price_when_profit_floor_is_broken() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "price.amount_minor < required_sale" in source
    assert "mapping.suspended_for_profit = True" in source
    assert "price.is_active = False" in source


def test_exception_queue_supports_filters_pagination_and_audit() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:exceptions:failed:0" in source
    assert "plugin:prodseller:exceptions:unknown:0" in source
    assert "plugin:prodseller:audit:" in source
    service = Path("app/services/supplier_orders.py").read_text()
    assert "offset: int = 0" in service
    assert "get_supplier_order_summary" in service


def test_profit_suspension_notifies_and_offers_safe_repricing() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "_notify_profit_suspensions" in source
    assert "plugin:prodseller:suggest:" in source
    assert "plugin:prodseller:custom:" in source
    assert "售价低于当前最低安全售价" in source


def test_supplier_purchase_preflight_checks_live_cost_stock_and_email() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "await self.client.get_product" in source
    assert "current_cost_minor > context.sale_amount_minor" in source
    assert "stock < context.quantity" in source
    assert 'remote["requiresEmailActivation"]' in source
    assert "quantity=context.quantity" in source


def test_supplier_fulfillment_supports_multiple_encrypted_cards() -> None:
    source = Path("app/services/supplier_fulfillment.py").read_text()
    assert "len(delivered_secrets) != item.quantity" in source
    assert "for secret in delivered_secrets" in source
    assert "len(set(delivered_secrets))" in source


def test_supplier_adapter_contract_is_independent_from_prodseller() -> None:
    source = Path("app/plugins/supplier.py").read_text()
    assert "class SupplierAdapter(Protocol)" in source
    assert "async def get_product" in source
    assert "async def create_order" in source
    assert "async def list_orders" in source
    assert "prodseller" not in source.lower()


def test_supplier_errors_are_classified_before_recovery_polling() -> None:
    plugin = object.__new__(ProdsellerPlugin)
    assert plugin._failure_status(ProdsellerError("stock", status=409)) == "failed"
    assert plugin._failure_status(ProdsellerError("rate", status=429)) == "unknown"
    assert plugin._failure_status(ProdsellerError("server", status=500)) == "unknown"


def test_supplier_polling_honors_retry_after() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "exc.rate_limit.retry_after" in source
    assert "delay = max(delay, exc.rate_limit.retry_after)" in source


def test_missing_external_order_reconciliation_is_conservative() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "_reconcile_missing_external_ids" in source
    assert "await self.client.list_orders(page=1, limit=200)" in source
    assert 'product.get("id") != context.external_product_id' in source
    assert 'order.get("quantity", 1) != context.quantity' in source
    assert "len(candidates) != 1" in source
    assert "多个候选" in source


def test_upstream_reconciliation_rejects_invalid_or_naive_times() -> None:
    assert ProdsellerPlugin._parse_upstream_datetime(None) is None
    assert ProdsellerPlugin._parse_upstream_datetime("bad") is None
    assert ProdsellerPlugin._parse_upstream_datetime("2026-01-01T00:00:00") is None
    parsed = ProdsellerPlugin._parse_upstream_datetime("2026-01-01T00:00:00Z")
    assert parsed is not None and parsed.tzinfo is not None


def test_background_tasks_reserve_api_quota_for_fulfillment() -> None:
    plugin = object.__new__(ProdsellerPlugin)
    plugin.settings = ProdsellerSettings(
        prodseller_api_key="test", prodseller_rate_limit_reserve=20
    )
    class Client:
        pass
    from plugins.prodseller.client import RateLimit
    plugin.client = Client()
    plugin.client.rate_limit = RateLimit(limit=300, remaining=20)
    assert plugin._rate_limit_exhausted() is True
    plugin.client.rate_limit = RateLimit(limit=300, remaining=21)
    assert plugin._rate_limit_exhausted() is False


def test_rate_limit_reserve_has_safe_default() -> None:
    settings = ProdsellerSettings(prodseller_api_key="test")
    assert settings.prodseller_rate_limit_reserve == 20
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "if not missing or self._rate_limit_exhausted()" in source


def test_admin_can_review_and_select_ambiguous_supplier_candidates() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:candidates:" in source
    assert "plugin:prodseller:selectcandidate:" in source
    assert "_reconciliation_candidates" in source
    assert "仅在人工核对确认后选择" in source
    assert "record_supplier_recovery" in source
    assert "该上游订单不再符合安全匹配条件" in source


def test_normalized_supplier_metadata_is_json_serializable() -> None:
    import json
    remote = {"id": "p1", "price": Decimal("0.02"), "nested": {"ok": True}}
    safe = ProdsellerPlugin._json_safe_remote(remote)
    assert safe["price"] == "0.02"
    json.dumps(safe)


def test_supplier_product_import_is_admin_audited() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert 'action="supplier_product_import"' in source
    assert '"external_product_id": remote["id"]' in source
    assert '"cost_amount_minor": cost_minor' in source
    assert "admin_telegram_id=callback.from_user.id" in source
    assert "admin_telegram_id=message.from_user.id" in source


def test_supplier_health_panel_and_sync_audit_exist() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:health" in source
    assert "ProdSeller 健康状态" in source
    assert "本地映射" in source
    assert "请求额度" in source
    assert 'action="supplier_manual_sync"' in source
    assert '"updated_mappings": updated' in source
    assert '"profit_suspended": len(suspended)' in source


def test_admin_can_inspect_sync_and_suspend_supplier_mapping() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:mapping:" in source
    assert "plugin:prodseller:syncmapping:" in source
    assert "plugin:prodseller:togglemapping:" in source
    assert "单件毛利" in source
    assert "最近同步" in source
    assert 'action="supplier_mapping_toggle"' in source
    assert "await self.client.get_product(mapping.external_product_id)" in source


def test_suspended_supplier_mapping_is_not_sellable_or_fulfillable() -> None:
    orders = Path("app/services/orders.py").read_text()
    payments = Path("app/services/plugin_payments.py").read_text()
    assert "not supplier.suspended_for_profit" in orders
    assert "not item.suspended_for_profit" in orders
    assert orders.count("SupplierProduct.suspended_for_profit.is_(False)") >= 2
    assert "SupplierProduct.suspended_for_profit.is_(False)" in payments


def test_supplier_route_is_snapshotted_and_cost_ordered() -> None:
    source = Path("app/services/supplier_routing.py").read_text()
    assert "cost_amount_minor.asc()" in source
    assert "suspended_for_profit.is_(False)" in source
    assert "requires_customer_email.is_(False)" in source
    assert '"route_snapshot"' in source
    assert "SupplierOrder(" in source


def test_paid_fulfillment_reserves_supplier_route() -> None:
    orders = Path("app/services/orders.py").read_text()
    payments = Path("app/services/plugin_payments.py").read_text()
    assert "reserve_supplier_route" in orders
    assert "reserve_supplier_route" in payments
    fulfillment = Path("app/services/supplier_fulfillment.py").read_text()
    assert "SupplierOrder.provider == provider" in fulfillment


def test_supplier_routes_by_priority_then_cost() -> None:
    source = Path("app/services/supplier_routing.py").read_text()
    assert "SupplierProduct.routing_priority.asc()" in source
    assert source.index("routing_priority.asc()") < source.index("cost_amount_minor.asc()")
    assert '"selection_reason": "priority_then_lowest_cost"' in source


def test_admin_can_set_audited_supplier_routing_priority() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "plugin:prodseller:priority:" in source
    assert "路由优先级" in source
    assert 'action="supplier_routing_priority"' in source
    assert '"old_priority": old_priority' in source
    assert '"new_priority": priority' in source


def test_routes_only_to_loaded_supplier_plugins() -> None:
    registry = Path("app/plugins/registry.py").read_text()
    assert "def supplier_providers" in registry
    assert 'getattr(plugin, "supplier_provider", False)' in registry
    plugin = Path("plugins/prodseller/plugin.py").read_text()
    assert "supplier_provider = True" in plugin
    routing = Path("app/services/supplier_routing.py").read_text()
    assert "available_providers" in routing
    assert "SupplierProduct.provider.in_(available_providers)" in routing


def test_supplier_management_is_grouped_under_one_admin_menu() -> None:
    admin = Path("app/handlers/admin.py").read_text()
    plugin = Path("plugins/prodseller/plugin.py").read_text()
    registry = Path("app/plugins/registry.py").read_text()
    assert 'text="🏭 供应商管理"' in admin
    assert 'F.data == "adm:suppliers"' in admin
    assert "supplier_plugins" in registry
    assert "def supplier_admin_buttons" in plugin
    assert "def admin_home_buttons" in plugin
    assert "return []" in plugin
    for callback in (
        "plugin:prodseller:browse:0",
        "plugin:prodseller:sync",
        "plugin:prodseller:exceptions:all:0",
        "plugin:prodseller:balance",
        "plugin:prodseller:health",
    ):
        assert callback in plugin


def test_prodseller_uses_neutral_usdt_sale_price() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert 'ProductPrice.provider == "usdt"' in source
    assert 'provider="usdt"' in source
    assert 'ProductPrice.provider == "bepusdt"' not in source


def test_usdt_methods_require_enablement_and_destination() -> None:
    source = Path("app/services/usdt_methods.py").read_text()
    for method in ("usdt_binance", "usdt_bep20", "usdt_trc20", "usdt_arbitrum"):
        assert method in source
    assert "self.enabled and bool(self.destination.strip())" in source


def test_topup_menu_uses_only_configured_usdt_methods() -> None:
    catalog = Path("app/handlers/catalog.py").read_text()
    assert "available_usdt_methods(settings)" in catalog
    assert 'callback_data=f"topup:start:{method.id}"' in catalog
    assert "topup:start:cryptobot" not in catalog
    topups = Path("app/services/topups.py").read_text()
    for method in ("usdt_binance", "usdt_bep20", "usdt_trc20", "usdt_arbitrum"):
        assert method in topups
    assert "unsupported top-up method" in topups


def test_direct_usdt_purchase_uses_neutral_price_and_manual_proof() -> None:
    catalog = Path("app/handlers/catalog.py").read_text()
    orders = Path("app/services/orders.py").read_text()
    payments = Path("app/services/plugin_payments.py").read_text()
    assert 'F.data.startswith("usdt:confirm:")' in catalog
    assert 'F.data.startswith("usdt:create:")' in catalog
    assert "ManualUsdtPurchase.proof" in catalog
    assert '"usdt" if payment_provider.startswith("usdt_")' in orders
    assert 'payment.status = "proof_pending"' in payments
    assert "PluginPayment.transaction_id == proof" in payments


def test_admin_reviews_manual_usdt_orders_safely() -> None:
    admin = Path("app/handlers/admin.py").read_text()
    payments = Path("app/services/plugin_payments.py").read_text()
    assert 'text="₮ USDT 订单审核"' in admin
    assert 'F.data == "adm:usdt:list"' in admin
    assert "approve_manual_usdt_payment" in admin
    assert "reject_manual_usdt_payment" in admin
    assert 'PluginPayment.status == "proof_pending"' in payments
    assert 'action="manual_usdt_payment_review"' in payments
    assert "card.status = CardStatus.AVAILABLE" in payments
    assert "reserve_supplier_route" in payments


def test_usdt_review_snapshots_destination_and_requires_confirmation() -> None:
    catalog = Path("app/handlers/catalog.py").read_text()
    admin = Path("app/handlers/admin.py").read_text()
    cleanup = Path("app/services/cleanup.py").read_text()
    assert '"destination": method.destination' in catalog
    assert "record.destination" in admin
    assert 'F.data.startswith("adm:usdt:ask:")' in admin
    assert "create_confirmation" in admin
    assert "consume_confirmation" in admin
    assert '"proof_pending"' in cleanup


def test_manual_usdt_approval_ends_lookup_transaction_before_fulfillment() -> None:
    source = Path("app/services/plugin_payments.py").read_text()
    start = source.index("async def approve_manual_usdt_payment")
    body = source[start:source.index("async def reject_manual_usdt_payment", start)]
    assert "await session.rollback()" in body
    assert body.index("await session.rollback()") < body.index("await fulfill_plugin_payment(")
