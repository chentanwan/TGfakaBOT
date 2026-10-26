from pathlib import Path

from plugins.bepusdt.signing import generate_signature, verify_signature


def test_plugin_loader_is_configuration_driven() -> None:
    source = Path("app/main.py").read_text()
    assert "configure_plugins(settings.plugin_modules)" in source
    assert "start_plugin_http_server" in source


def test_bepusdt_is_outside_core_package() -> None:
    assert Path("plugins/bepusdt/plugin.py").exists()
    core_sources = "\n".join(
        path.read_text() for path in Path("app/services").glob("*.py")
    )
    assert "create-transaction" not in core_sources
    assert "BEPUSDT_API_TOKEN" not in core_sources


def test_bepusdt_signing_is_order_independent() -> None:
    token = "secret"
    left = {"order_id": "A1", "amount": 9.9, "fiat": "CNY"}
    right = {"fiat": "CNY", "amount": 9.9, "order_id": "A1"}
    assert generate_signature(left, token) == generate_signature(right, token)
    signed = {**left, "signature": generate_signature(left, token)}
    assert verify_signature(signed, token)
    assert not verify_signature({**signed, "amount": 10}, token)


def test_callback_requires_signature_and_transaction_id() -> None:
    source = Path("plugins/bepusdt/plugin.py").read_text()
    assert "verify_signature" in source
    assert "block_transaction_id" in source
    assert "fulfill_plugin_payment" in source
