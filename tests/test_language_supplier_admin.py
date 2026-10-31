from pathlib import Path


def test_chinese_is_default_and_only_chinese_english_are_switchable() -> None:
    config = Path("app/core/config.py").read_text()
    i18n = Path("app/core/i18n.py").read_text()
    catalog = Path("app/handlers/catalog.py").read_text()
    accounts = Path("app/services/accounts.py").read_text()
    models = Path("app/db/models.py").read_text()
    assert 'bot_language: str = "zh"' in config
    assert 'SUPPORTED_LANGUAGES = ("zh", "en")' in i18n
    assert 'for code in ("zh", "en")' in catalog
    assert 'language = "zh"' in accounts
    assert 'default="zh"' in models


def test_supplier_catalog_puts_product_information_in_buttons() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    browse = source[
        source.index("async def _browse") : source.index("async def _import_from_browser")
    ]
    assert "lines.append(" not in browse
    assert "item['name'][:26]" in browse
    assert "cost_minor / 100" in browse
    assert "sale_minor / 100" in browse
    assert 'stock = "🟢" if item["inStock"] else "🔴"' in browse


def test_admin_home_only_contains_daily_operations() -> None:
    source = Path("app/handlers/admin.py").read_text()
    block = source[
        source.index("def admin_keyboard") : source.index('@router.message(Command("admin"))')
    ]
    for callback in (
        "adm:list",
        "adm:suppliers",
        "plugin:prices:list",
        "adm:usdt:health",
        "adm:usdt:list",
        "adm:topups:list",
        "plugin:adminmgr:list",
    ):
        assert callback in block
    for callback in (
        "adm:new",
        "adm:user:find",
        "adm:broadcast",
        "adm:pex:list",
        "adm:refunds:pending",
        "adm:charges:list",
    ):
        assert callback not in block


def test_product_detail_always_explains_purchase_availability() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'callback_data="shop:unavailable"' in source
    assert '@router.callback_query(F.data == "shop:unavailable")' in source
    assert "暂无可用支付方式" in source
    assert "暂时缺货" in source


def test_safe_supplier_prices_are_restored_for_purchase() -> None:
    migration = Path("migrations/versions/81b972e6c4ad_restore_safe_supplier_prices.py").read_text()
    plugin = Path("plugins/prodseller/plugin.py").read_text()
    assert "SET is_active = true" in migration
    assert "minimum_safe_minor" in migration
    assert 'price.is_active = bool(remote["inStock"])' in plugin
    assert 'pricing_mode = "auto_markup"' in plugin
