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


def test_price_manager_only_exposes_usd_price_types() -> None:
    source = Path("plugins/price_manager/plugin.py").read_text()
    assert 'provider not in {"usdt", "balance"}' in source
    assert "设置 Stars" not in source
    assert "sku.price_xtr = amount" not in source
    assert "set_product_plugin_price(" in source
    assert 'currency="USD"' in source
    assert 'action="product_price_set"' in source
    assert 'action="product_price_disable"' in source


def test_customer_catalog_does_not_offer_stars_payment() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'text=f"⭐ Buy' not in source
    assert 'callback_data=f"buy:' not in source
    assert "send_invoice(" not in source
    assert 'return "USD price unavailable"' in source
    assert "display_price_usd_minor" in source


def test_new_products_use_usd_prices_and_disable_stars() -> None:
    service = Path("app/services/admin.py").read_text()
    handler = Path("app/handlers/admin.py").read_text()
    assert "price_usd_minor" in service
    assert "stars_enabled=False" in service
    assert 'provider=provider, currency="USD"' in service
    assert "请输入美元价格" in handler
    assert "Stars：" not in handler
