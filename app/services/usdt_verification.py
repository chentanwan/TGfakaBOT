from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal

import aiohttp

from app.core.config import Settings

logger = logging.getLogger(__name__)
_TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class VerificationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class VerificationResult:
    state: str
    confirmations: int = 0
    amount_minor: int | None = None
    reason: str | None = None
    raw: dict | None = None


def automatic_verification_available(settings: Settings, method_id: str) -> bool:
    if not settings.usdt_auto_verify_enabled:
        return False
    if method_id == "usdt_bep20":
        return bool(settings.usdt_bep20_rpc_url and settings.usdt_bep20_token_contract)
    if method_id == "usdt_arbitrum":
        return bool(settings.usdt_arbitrum_rpc_url and settings.usdt_arbitrum_token_contract)
    if method_id == "usdt_trc20":
        return bool(settings.usdt_tron_api_url and settings.usdt_trc20_token_contract)
    return False


def _tron_hex(address: str) -> str:
    number = 0
    for character in address:
        number = number * 58 + _B58_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    decoded = b"\0" * (len(address) - len(address.lstrip("1"))) + decoded
    if len(decoded) != 25:
        raise VerificationError("TRON 地址长度无效")
    payload, checksum = decoded[:-4], decoded[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected or payload[0] != 0x41:
        raise VerificationError("TRON 地址校验失败")
    return payload.hex()


def _amount_minor(raw_amount: int, decimals: int) -> int:
    value = Decimal(raw_amount) * Decimal(100) / (Decimal(10) ** decimals)
    if value != value.to_integral_value():
        raise VerificationError("到账金额无法精确换算为美分")
    return int(value)


async def verify_usdt_payment(
    settings: Settings,
    *,
    method_id: str,
    proof: str,
    destination: str,
    expected_amount_minor: int,
) -> VerificationResult:
    if method_id in {"usdt_bep20", "usdt_arbitrum"}:
        return await _verify_evm(
            settings,
            method_id=method_id,
            tx_hash=proof,
            destination=destination,
            expected_amount_minor=expected_amount_minor,
        )
    if method_id == "usdt_trc20":
        return await _verify_tron(
            settings,
            tx_hash=proof,
            destination=destination,
            expected_amount_minor=expected_amount_minor,
        )
    return VerificationResult("unsupported", reason="该支付方式尚无自动核验接口")


async def _rpc(session: aiohttp.ClientSession, url: str, method: str, params: list) -> object:
    async with session.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    if payload.get("error"):
        raise VerificationError(str(payload["error"]))
    return payload.get("result")


async def _verify_evm(
    settings: Settings,
    *,
    method_id: str,
    tx_hash: str,
    destination: str,
    expected_amount_minor: int,
) -> VerificationResult:
    rpc_url = (
        settings.usdt_bep20_rpc_url
        if method_id == "usdt_bep20"
        else settings.usdt_arbitrum_rpc_url
    )
    token = (
        settings.usdt_bep20_token_contract
        if method_id == "usdt_bep20"
        else settings.usdt_arbitrum_token_contract
    ).lower()
    timeout = aiohttp.ClientTimeout(total=settings.usdt_verify_timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            receipt, latest = await asyncio.gather(
                _rpc(session, rpc_url, "eth_getTransactionReceipt", [tx_hash]),
                _rpc(session, rpc_url, "eth_blockNumber", []),
            )
    except (aiohttp.ClientError, TimeoutError, VerificationError) as exc:
        logger.warning("EVM USDT verification unavailable: %s", exc)
        return VerificationResult("pending", reason="链上查询服务暂时不可用")
    if receipt is None:
        return VerificationResult("pending", reason="交易尚未上链")
    if receipt.get("status") != "0x1":
        return VerificationResult("rejected", reason="链上交易执行失败", raw=receipt)
    block = int(receipt["blockNumber"], 16)
    confirmations = max(0, int(latest, 16) - block + 1)
    destination_topic = destination.lower().removeprefix("0x").rjust(64, "0")
    amounts = []
    for log in receipt.get("logs", []):
        topics = [str(item).lower().removeprefix("0x") for item in log.get("topics", [])]
        if (
            str(log.get("address", "")).lower() == token
            and len(topics) >= 3
            and topics[0] == _TRANSFER_TOPIC
            and topics[2] == destination_topic
        ):
            amounts.append(int(str(log.get("data", "0x0")), 16))
    if not amounts:
        return VerificationResult(
            "rejected", confirmations, reason="未找到发往收款地址的 USDT 转账"
        )
    amount_minor = _amount_minor(sum(amounts), settings.usdt_token_decimals)
    if amount_minor != expected_amount_minor:
        return VerificationResult("rejected", confirmations, amount_minor, "到账金额不匹配")
    if confirmations < settings.usdt_evm_confirmations:
        return VerificationResult("pending", confirmations, amount_minor, "等待链上确认")
    return VerificationResult("confirmed", confirmations, amount_minor, raw=receipt)


async def _verify_tron(
    settings: Settings,
    *,
    tx_hash: str,
    destination: str,
    expected_amount_minor: int,
) -> VerificationResult:
    headers = {"TRON-PRO-API-KEY": settings.usdt_tron_api_key} if settings.usdt_tron_api_key else {}
    base = settings.usdt_tron_api_url.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=settings.usdt_verify_timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(
                f"{base}/wallet/gettransactioninfobyid", json={"value": tx_hash}
            ) as response:
                response.raise_for_status()
                info = await response.json()
            async with session.post(f"{base}/wallet/getnowblock", json={}) as response:
                response.raise_for_status()
                current = await response.json()
    except (aiohttp.ClientError, TimeoutError, VerificationError) as exc:
        logger.warning("TRON USDT verification unavailable: %s", exc)
        return VerificationResult("pending", reason="链上查询服务暂时不可用")
    if not info or not info.get("blockNumber"):
        return VerificationResult("pending", reason="交易尚未上链")
    if info.get("receipt", {}).get("result", "SUCCESS") != "SUCCESS":
        return VerificationResult("rejected", reason="链上交易执行失败", raw=info)
    confirmations = max(
        0,
        int(current["block_header"]["raw_data"]["number"])
        - int(info["blockNumber"])
        + 1,
    )
    token_hex = _tron_hex(settings.usdt_trc20_token_contract).removeprefix("41")
    destination_hex = _tron_hex(destination).removeprefix("41").rjust(64, "0")
    amounts = []
    for log in info.get("log", []):
        topics = [str(item).lower().removeprefix("0x") for item in log.get("topics", [])]
        if (
            str(log.get("address", "")).lower().removeprefix("41") == token_hex
            and len(topics) >= 3
            and topics[0] == _TRANSFER_TOPIC
            and topics[2] == destination_hex
        ):
            amounts.append(int(str(log.get("data", "0")), 16))
    if not amounts:
        return VerificationResult(
            "rejected", confirmations, reason="未找到发往收款地址的 USDT 转账"
        )
    amount_minor = _amount_minor(sum(amounts), settings.usdt_token_decimals)
    if amount_minor != expected_amount_minor:
        return VerificationResult("rejected", confirmations, amount_minor, "到账金额不匹配")
    if confirmations < settings.usdt_tron_confirmations:
        return VerificationResult("pending", confirmations, amount_minor, "等待链上确认")
    return VerificationResult("confirmed", confirmations, amount_minor, raw=info)
