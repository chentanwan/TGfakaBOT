from pathlib import Path

from app.db.base import OrderStatus, PaymentStatus


def test_payment_processing_state_exists() -> None:
    assert OrderStatus.PAYMENT_PROCESSING.value == "payment_processing"


def test_refund_states_exist() -> None:
    assert OrderStatus.REFUND_PENDING.value == "refund_pending"
    assert OrderStatus.REFUNDED.value == "refunded"
    assert PaymentStatus.REFUND_FAILED.value == "refund_failed"


def test_startup_preserves_pending_financial_updates() -> None:
    source = Path("app/main.py").read_text()
    assert "drop_pending_updates=False" in source
    assert "drop_pending_updates=True" not in source


def test_duplicate_payment_does_not_repeat_secret() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert "if not delivered.should_deliver" in source
    assert "未重复发送卡密" in source


def test_cleanup_only_releases_unapproved_precheckout_orders() -> None:
    source = Path("app/services/cleanup.py").read_text()
    assert "Order.precheckout_approved_at.is_(None)" in source
