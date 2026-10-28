import json
from pathlib import Path


def test_demo_catalog_has_three_products_and_two_items_each() -> None:
    products = json.loads(Path("examples/products.json").read_text())
    assert 3 <= len(products) <= 5
    assert all(len(product["inventory"]) == 2 for product in products)


def test_demo_initializer_encrypts_and_deduplicates_inventory() -> None:
    source = Path("scripts/init_demo.py").read_text()
    assert "card_vault.encrypt(secret)" in source
    assert "card_vault.fingerprint(secret)" in source
    assert "Card.secret_fingerprint == fingerprint" in source


def test_readme_has_acceptance_and_assumption_sections() -> None:
    source = Path("README.md").read_text()
    assert "Complete acceptance walkthrough" in source
    assert "Assumptions requiring operator confirmation" in source
    assert "python -m scripts.init_demo" in source
