from pathlib import Path


def test_topup_review_is_transactional_and_idempotent() -> None:
    source = Path("app/services/topups.py").read_text()
    assert "async with session.begin()" in source
    assert "with_for_update()" in source
    assert 'record.status != "pending"' in source
    assert "user.balance_minor += record.amount_minor" in source


def test_duplicate_payment_proof_is_rejected() -> None:
    models = Path("app/db/models.py").read_text()
    service = Path("app/services/topups.py").read_text()
    assert 'UniqueConstraint("method", "proof")' in models
    assert "this payment proof has already been submitted" in service


def test_manual_topup_has_cancel_and_all_method_buttons() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    for text in ("Binance Pay", "CryptoBot", "USDT BEP20 (BSC)", "topup:cancel"):
        assert text in source
