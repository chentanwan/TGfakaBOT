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
