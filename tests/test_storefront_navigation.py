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


def test_usdt_requires_purchase_confirmation() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert "usdt:confirm:" in source
    assert "usdt:create:" in source


def test_catalog_service_exposes_categories_and_order_status() -> None:
    source = Path("app/services/orders.py").read_text()
    assert "class CatalogCategory" in source
    assert "list_catalog_categories" in source
    assert "status=order.status.value" in source


def test_product_list_is_unpaginated_and_stock_styled() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'style="success" if available else "danger"' in source
    assert "page_size =" not in source
    assert "Next ➡️" not in source
    assert "Out of stock" in source


def test_out_of_stock_supplier_products_remain_visible() -> None:
    source = Path("app/services/orders.py").read_text()
    assert "select(SupplierProduct).where(SupplierProduct.sku_id.in_(sku_ids))" in source
    assert "if product.stock < 1 and not product.supplier_stock" not in source
