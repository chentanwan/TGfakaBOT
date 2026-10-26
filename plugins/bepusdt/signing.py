import hashlib
import hmac
import math
from decimal import Decimal
from typing import Any


class SignatureError(ValueError):
    pass


def _signature_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, (int, float)):
        raise SignatureError("签名仅支持 JSON 标量")
    number = float(value)
    if not math.isfinite(number):
        raise SignatureError("签名数字必须有限")
    if number == 0:
        return "-0" if math.copysign(1, number) < 0 else "0"
    decimal = Decimal(repr(number))
    if -4 <= decimal.adjusted() < 6:
        text = format(decimal, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    mantissa, exponent = format(decimal, "e").split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent_number = int(exponent)
    return f"{mantissa}e{'+' if exponent_number >= 0 else '-'}{abs(exponent_number):02d}"


def generate_signature(params: dict[str, Any], api_token: str) -> str:
    filtered = {
        key: value
        for key, value in params.items()
        if key != "signature" and value not in (None, "")
    }
    text = "&".join(
        f"{key}={_signature_value(value)}" for key, value in sorted(filtered.items())
    ).rstrip("&")
    return hashlib.md5((text + api_token).encode("utf-8")).hexdigest()  # noqa: S324


def verify_signature(params: dict[str, Any], api_token: str) -> bool:
    signature = params.get("signature")
    if not isinstance(signature, str) or not signature.isascii():
        return False
    try:
        expected = generate_signature(params, api_token)
    except SignatureError:
        return False
    return hmac.compare_digest(expected, signature.lower())
