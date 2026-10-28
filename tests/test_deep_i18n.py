from pathlib import Path

from app.core.i18n import MESSAGES, SUPPORTED_LANGUAGES


def test_purchase_workflow_keys_exist_in_all_languages() -> None:
    required = {
        "choose_category", "choose_product", "in_stock", "out_of_stock",
        "choose_quantity", "confirm_purchase", "quantity", "unit_price",
        "total", "delivery", "purchase_completed", "save_delivery",
    }
    for language in SUPPORTED_LANGUAGES:
        assert required <= set(MESSAGES[language])


def test_balance_purchase_uses_localized_workflow_text() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    for key in ("choose_quantity", "confirm_purchase", "purchase_completed", "save_delivery"):
        assert f"'{key}'" in source or f'"{key}"' in source
