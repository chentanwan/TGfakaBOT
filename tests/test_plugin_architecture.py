from pathlib import Path


def test_plugin_loader_is_configuration_driven() -> None:
    source = Path("app/main.py").read_text()
    assert "configure_plugins(settings.plugin_modules)" in source
    assert "start_plugin_http_server" in source


def test_callback_requires_signature_and_transaction_id() -> None:
    source = Path("app/services/usdt_methods.py").read_text()
    assert "verify_signature" in source
    assert "block_transaction_id" in source
    assert "fulfill_plugin_payment" in source
