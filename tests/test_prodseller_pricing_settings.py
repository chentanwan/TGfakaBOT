from decimal import Decimal
from pathlib import Path

import pytest

from plugins.prodseller.pricing_settings import validate_profit_percent


def test_profit_percent_validation() -> None:
    assert validate_profit_percent("20") == Decimal("2E+1")
    assert validate_profit_percent("12.50") == Decimal("12.5")
    assert validate_profit_percent("0") == Decimal("0")
    with pytest.raises(ValueError, match="0%–1000%"):
        validate_profit_percent("-1")
    with pytest.raises(ValueError, match="0%–1000%"):
        validate_profit_percent("1001")
    with pytest.raises(ValueError, match="两位小数"):
        validate_profit_percent("12.345")


def test_prodseller_exposes_persistent_pricing_settings() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    settings = Path("plugins/prodseller/pricing_settings.py").read_text()
    assert 'text="⚙️ 定价模式"' in source
    assert 'callback_data="ps:set:percent"' in source
    assert "set_profit_percent" in source
    assert "self._profit_percent = percent" in source
    assert '"updated_products": updated' in source
    assert "key=PRICING_SETTINGS_KEY" in settings
    assert '"profit_percent": str(percent)' in settings


def test_custom_ratio_drives_import_switch_and_sync() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert '"profit_percent": str(self._profit_percent)' in source
    assert "value = self._profit_percent" in source
    assert "Decimal(100) + self._profit_percent" in source
    assert 'metadata.get("profit_percent", self._profit_percent)' in source
