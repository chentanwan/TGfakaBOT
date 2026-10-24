from pathlib import Path


def test_admin_captures_telegram_entities_as_html() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert "message.html_text" in source
    assert "description_html=description_html" in source


def test_product_detail_renders_saved_html() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'F.data.startswith("buy:")' in source
    assert "product.description_html" in source
    assert 'F.data.startswith("pay:")' in source


def test_invoice_still_uses_plain_text_description() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert "description=order.description[:255]" in source
