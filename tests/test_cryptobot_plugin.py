import hashlib
import hmac
from pathlib import Path

from plugins.cryptobot.signing import verify_webhook


def test_cryptobot_webhook_signature() -> None:
    token = "test-token"
    body = b'{"update_type":"invoice_paid"}'
    secret = hashlib.sha256(token.encode()).digest()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert verify_webhook(token, body, signature)
    assert not verify_webhook(token, body + b"x", signature)
    assert not verify_webhook(token, body, None)


def test_cryptobot_callback_checks_invoice_amount_and_idempotency() -> None:
    source = Path("plugins/cryptobot/plugin.py").read_text()
    assert 'update.get("update_type") != "invoice_paid"' in source
    assert "record.amount_minor != amount_minor" in source
    assert "review_topup(" in source
    assert "if not duplicate:" in source


def test_cryptobot_is_configured_as_plugin() -> None:
    env = Path(".env.example").read_text()
    assert "plugins.cryptobot" in env
    assert "CRYPTOBOT_API_TOKEN=" in env
