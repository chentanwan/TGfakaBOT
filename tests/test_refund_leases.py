from pathlib import Path

from app.db.base import PaymentStatus


def test_refund_unknown_state_exists() -> None:
    assert PaymentStatus.REFUND_UNKNOWN.value == "refund_unknown"


def test_linked_refund_uses_fencing_token() -> None:
    service = Path("app/services/payment_exceptions.py").read_text()
    handler = Path("app/handlers/payment_admin.py").read_text()
    assert "refund_claim_token" in service
    assert "refund_lease_expires_at" in service
    assert "claim_token=request.claim_token" in handler


def test_orphan_refund_is_charge_driven() -> None:
    service = Path("app/services/charge_exceptions.py").read_text()
    assert "charge_id: uuid.UUID" in service
    assert "PaymentCharge.order_id.is_(None)" in service
    assert "telegram_user_id" in service


def test_timeout_is_quarantined_not_retried() -> None:
    service = Path("app/services/charge_exceptions.py").read_text()
    assert "PaymentStatus.REFUND_UNKNOWN" in service
    assert "禁止自动重试" in service
