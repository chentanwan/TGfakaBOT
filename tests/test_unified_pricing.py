from pathlib import Path


def test_price_manager_uses_one_usd_price_for_both_payments() -> None:
    plugin = Path("plugins/price_manager/plugin.py").read_text()
    service = Path("app/services/product_prices.py").read_text()
    assert "set_product_usd_price" in plugin
    assert 'for provider in ("usdt", "balance")' in service
    assert "设置 USDT" not in plugin
    assert "设置余额" not in plugin
    assert "停用 USDT" not in plugin
    assert "停用余额" not in plugin
    assert "✏️ 设置美元售价" in plugin
    assert "⏸ 停用全部支付" in plugin
    assert "统一使用美元价格，不再提供 Telegram Stars 付款。" not in plugin


def test_supplier_supports_fixed_percent_and_manual_pricing() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert '"auto_fixed": "自动定价 · 固定利润"' in source
    assert '"auto_percent": "自动定价 · 比例利润"' in source
    assert '"manual_protected": "手动定价"' in source
    assert "💵 固定利润" in source
    assert "📈 比例利润" in source
    assert "🤖 自动定价" in source
    assert "✏️ 手动定价" in source
    assert "profit_fixed_minor" in source
    assert "profit_percent" in source


def test_supplier_prices_are_mirrored_to_balance() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    migration = Path("migrations/versions/93c2e1ab7d4f_unify_usd_product_prices.py").read_text()
    assert 'provider="balance"' in source
    assert "balance.amount_minor = price.amount_minor" in source
    assert "balance.is_active = price.is_active" in source
    assert "INSERT INTO product_prices" in migration
    assert "balance.provider = 'balance'" in migration
