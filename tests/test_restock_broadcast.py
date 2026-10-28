from pathlib import Path


def test_restock_result_contains_broadcast_values() -> None:
    source = Path("app/services/admin.py").read_text()
    for value in ("inserted", "current_stock", "unit_price_xtr", "product_name"):
        assert value in source


def test_restock_broadcast_has_required_format() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert "➕ Added:" in source
    assert "📦 Current stock:" in source
    assert "💵 Unit price:" in source
    assert "restock_broadcast_chat_id" in source
