from pathlib import Path


def test_balance_purchase_uses_single_transaction_and_locks() -> None:
    source = Path("app/services/accounts.py").read_text()
    assert "async with session.begin()" in source
    assert ".with_for_update(skip_locked=True)" in source
    assert "user.balance_minor -= total" in source
    assert "card.status = CardStatus.DELIVERED" in source


def test_balance_purchase_is_idempotent() -> None:
    source = Path("app/services/accounts.py").read_text()
    assert "Order.invoice_payload == idempotency_key" in source
    assert "duplicate=True" in source


def test_quantity_and_plan_ui_are_present() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    for value in (
        "balance:variant:",
        "balance:qty:",
        "balance:custom:",
        "balance:buy:",
        "Choose quantity",
    ):
        assert value in source


def test_multiple_cards_can_share_one_order_item() -> None:
    migration = Path("migrations/versions/b2371e927d73_multi_quantity_cards.py").read_text()
    assert 'op.drop_index("uq_cards_order_item"' in migration
    assert 'op.create_index("ix_cards_order_item"' in migration
