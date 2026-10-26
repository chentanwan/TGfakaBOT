from decimal import Decimal
from pathlib import Path

import pytest

from plugins.prodseller.client import ProdsellerClient, ProdsellerError


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
