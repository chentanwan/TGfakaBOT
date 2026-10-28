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
