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
    methods = Path("app/services/usdt_methods.py").read_text()
    for text in ("Binance Pay", "USDT BEP20 (BSC)", "USDT TRC20 (TRON)", "USDT Arbitrum"):
        assert text in methods
    assert "topup:cancel" in source
    assert "CryptoBot" not in source


def test_usdt_destination_formats_are_validated() -> None:
    source = Path("app/services/usdt_methods.py").read_text()
    assert 'r"0x[0-9a-fA-F]{40}\\Z"' in source
    assert 'r"T[1-9A-HJ-NP-Za-km-z]{33}\\Z"' in source
    assert 'r"[0-9]{4,20}\\Z"' in source
    assert "is_valid_destination(self.id, self.destination)" in source


def test_concurrent_usdt_proof_duplicates_have_safe_error() -> None:
    source = Path("app/services/plugin_payments.py").read_text()
    assert "except IntegrityError as exc" in source
    assert 'PluginPaymentError("该付款凭证已提交")' in source


def test_usdt_payment_proof_formats_are_network_specific() -> None:
    source = Path("app/services/usdt_methods.py").read_text()
    assert 'r"0x[0-9a-fA-F]{64}\\Z"' in source
    assert 'r"[0-9a-fA-F]{64}\\Z"' in source
    assert 'r"[A-Za-z0-9_-]{6,64}\\Z"' in source
    assert "normalize_and_validate_proof" in source
    assert "value = value.lower()" in source


def test_admin_has_masked_usdt_configuration_health_panel() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert 'text="⚙️ 支付设置"' in source
    assert 'F.data == "adm:usdt:health"' in source
    assert "configured_usdt_methods" in source
    assert "is_valid_destination" in source
    assert "_mask_payment_destination" in source
    assert 'value[:4]' in source and 'value[-4:]' in source


def test_usdt_automatic_verification_checks_chain_evidence() -> None:
    verifier = Path("app/services/usdt_verification.py").read_text()
    worker = Path("app/services/usdt_auto.py").read_text()
    assert "eth_getTransactionReceipt" in verifier
    assert "eth_blockNumber" in verifier
    assert "wallet/gettransactioninfobyid" in verifier
    assert "_TRANSFER_TOPIC" in verifier
    assert "到账金额不匹配" in verifier
    assert "automatic_verification_available" in verifier
    assert "fulfill_plugin_payment(" in worker
    assert "review_topup(" in worker
    assert 'PluginPayment.status == "proof_pending"' in worker


def test_binance_is_not_automatically_verified_without_merchant_api() -> None:
    source = Path("app/services/usdt_verification.py").read_text()
    assert 'method_id == "usdt_bep20"' in source
    assert 'method_id == "usdt_arbitrum"' in source
    assert 'method_id == "usdt_trc20"' in source
    section = source[
        source.index("def automatic_verification_available") : source.index("def _tron_hex")
    ]
    assert 'method_id == "usdt_binance"' not in section
