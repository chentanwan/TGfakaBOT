import hashlib
import hmac

from cryptography.fernet import Fernet

from app.core.config import get_settings


class CardVault:
    def __init__(self, encryption_key: str, fingerprint_key: str) -> None:
        self._fernet = Fernet(encryption_key.encode())
        self._fingerprint_key = fingerprint_key.encode()

    def encrypt(self, secret: str) -> bytes:
        return self._fernet.encrypt(secret.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    def fingerprint(self, secret: str) -> bytes:
        normalized = secret.strip().encode("utf-8")
        return hmac.new(self._fingerprint_key, normalized, hashlib.sha256).digest()


_settings = get_settings()
card_vault = CardVault(_settings.card_encryption_key, _settings.card_fingerprint_key)
