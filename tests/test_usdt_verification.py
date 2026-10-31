from decimal import Decimal

import pytest

from app.services.usdt_verification import (
    VerificationError,
    _amount_minor,
    automatic_verification_available,
    automatic_verification_configured,
)


class SettingsStub:
    usdt_auto_verify_enabled = True
    usdt_bep20_rpc_url = "https://bsc.example"
    usdt_bep20_token_contract = "0x" + "1" * 40
    usdt_arbitrum_rpc_url = "https://arb.example"
    usdt_arbitrum_token_contract = "0x" + "2" * 40
    usdt_tron_api_url = "https://tron.example"
    usdt_trc20_token_contract = "T" + "1" * 33


def test_automatic_verification_availability_is_channel_specific() -> None:
    settings = SettingsStub()
    assert automatic_verification_configured(settings, "usdt_bep20")
    assert automatic_verification_available(settings, "usdt_bep20")
    assert automatic_verification_available(settings, "usdt_arbitrum")
    assert automatic_verification_available(settings, "usdt_trc20")
    assert not automatic_verification_available(settings, "usdt_binance")
    settings.usdt_auto_verify_enabled = False
    assert not automatic_verification_available(settings, "usdt_bep20")


def test_usdt_amount_conversion_requires_exact_cents() -> None:
    assert _amount_minor(1_250_000, 6) == 125
    assert _amount_minor(Decimal("1000000"), 6) == 100
    with pytest.raises(VerificationError, match="精确换算"):
        _amount_minor(1, 6)
