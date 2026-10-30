from pathlib import Path


def test_customer_product_entry_opens_all_products() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert (
        'text=text(language, "products"),\n                    callback_data="shop:category:all"'
        in source
    )
    assert "await send_products(message, message.from_user.id, category_id=None)" in source
    assert 'text="📁 Categories", callback_data="shop:categories"' in source


def test_admin_menu_exposes_price_settings() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert 'text="💰 价格设置", callback_data="plugin:prices:list"' in source


def test_price_manager_supports_all_customer_price_types() -> None:
    source = Path("plugins/price_manager/plugin.py").read_text()
    assert 'provider not in {"stars", "usdt", "balance"}' in source
    assert "sku.price_xtr = amount" in source
    assert "sku.stars_enabled = True" in source
    assert "set_product_plugin_price(" in source
    assert 'action="product_price_set"' in source
    assert 'action="product_price_disable"' in source
