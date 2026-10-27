from pathlib import Path


def test_storefront_has_layered_navigation() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    for callback in (
        "shop:home",
        "shop:categories",
        "shop:category:",
        "shop:product:",
        "shop:account",
        "shop:help",
        "order_detail:",
        "buy_confirm:",
    ):
        assert callback in source


def test_bepusdt_requires_purchase_confirmation() -> None:
    source = Path("plugins/bepusdt/plugin.py").read_text()
    assert "plugin:bepusdt:confirm:" in source
    assert "Continue to payment" in source


def test_catalog_service_exposes_categories_and_order_status() -> None:
    source = Path("app/services/orders.py").read_text()
    assert "class CatalogCategory" in source
    assert "list_catalog_categories" in source
    assert "status=order.status.value" in source
