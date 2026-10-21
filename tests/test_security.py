from cryptography.fernet import Fernet

from app.core.security import CardVault


def test_card_vault_encrypts_and_fingerprints_deterministically() -> None:
    vault = CardVault(Fernet.generate_key().decode(), "fingerprint-secret")
    ciphertext = vault.encrypt("CARD-001")

    assert b"CARD-001" not in ciphertext
    assert vault.decrypt(ciphertext) == "CARD-001"
    assert vault.fingerprint(" CARD-001 ") == vault.fingerprint("CARD-001")
    assert vault.fingerprint("CARD-001") != vault.fingerprint("CARD-002")
