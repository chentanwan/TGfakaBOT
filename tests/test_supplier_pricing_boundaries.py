from pathlib import Path


def test_supplier_plugin_only_exposes_percentage_pricing() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert "📈 利润比例定价" in source
    assert "✏️ 手动定价" not in source
    assert "ps:c:" not in source
    assert "class CustomPrice" not in source
    assert 'pricing_mode = "auto_percent"' in source


def test_fixed_price_manager_excludes_supplier_products() -> None:
    source = Path("plugins/price_manager/plugin.py").read_text()
    assert "SupplierProduct" in source
    assert "Product.id.not_in(supplier_product_ids)" in source
    assert "供应商商品仅支持利润比例定价" in source
    assert "供应商商品由库存和利润比例自动控制" in source
    assert "💰 <b>固定价格设置</b>" in source
    assert "✏️ 设置固定售价" in source


def test_non_supplier_fixed_price_flow_remains_available() -> None:
    source = Path("plugins/price_manager/plugin.py").read_text()
    assert "set_product_usd_price(" in source
    assert 'metadata = {"pricing": "manual_protected"}' in source
    assert "USDT 与账户余额将同时启用" in source
