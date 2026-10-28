import hashlib
import hmac


def verify_webhook(token: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
