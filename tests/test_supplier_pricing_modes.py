from decimal import Decimal
from pathlib import Path

from plugins.prodseller.client import ProdsellerClient


def test_activation_instructions_are_normalized() -> None:
    order = ProdsellerClient._validate_order(
        {
            "orderId": "o1",
            "status": "paid",
            "quantity": 1,
            "amount": Decimal("1.25"),
            "activationInstructions": ["Activation is pending"],
        }
    )
    assert order["activationInstructions"] == ["Activation is pending"]


def test_supplier_has_auto_and_manual_pricing_modes() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert '"auto_fixed": "自动定价 · 固定利润"' in source
    assert '"auto_percent": "自动定价 · 比例利润"' in source
    assert '"pricing": "manual_protected"' in source
    assert 'pricing_mode in {"auto_fixed", "auto_percent", "auto_markup"}' in source
    assert "price.amount_minor = required_sale" in source
    assert "price.is_active = False" in source
    assert "定价模式：{pricing_label}" in source


def test_general_usdt_price_edits_become_protected_manual() -> None:
    source = Path("plugins/price_manager/plugin.py").read_text()
    assert '"pricing": "manual_protected"' in source
    assert "set_product_usd_price" in source
    assert "USDT 与余额" in source
