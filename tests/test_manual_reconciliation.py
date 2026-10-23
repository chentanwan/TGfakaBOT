from pathlib import Path


def test_orphan_reconciliation_requires_evidence_and_admin_audit() -> None:
    source = Path("app/services/charge_exceptions.py").read_text()
    assert "evidence_note" in source
    assert "resolved_by_telegram_id" in source
    assert "PaymentStatus.REFUND_UNKNOWN" in source


def test_linked_unknown_refunds_are_listed() -> None:
    source = Path("app/services/payment_exceptions.py").read_text()
    assert "PaymentStatus.REFUND_UNKNOWN" in source
    assert "reconcile_linked_refund" in source


def test_payment_receipt_and_matching_are_separate() -> None:
    source = Path("app/services/orders.py").read_text()
    assert "async def submit_payment_receipt" in source
    assert "async def match_and_fulfill_payment" in source
    handler = Path("app/handlers/catalog.py").read_text()
    assert "receipt = await submit_payment_receipt" in handler
    assert "charge_id=receipt.charge_id" in handler
