from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Setting
from app.services.usdt_methods import UsdtMethod, configured_usdt_methods, is_valid_destination

PAYMENT_METHODS_KEY = "usdt_payment_methods"
_ALLOWED_IDS = ("usdt_binance", "usdt_bep20", "usdt_trc20", "usdt_arbitrum")
_EDITABLE_FIELDS = {"label", "description", "destination", "minimum_topup_minor", "sort_order"}


def _serialize(method: UsdtMethod) -> dict:
    return asdict(method)


def _deserialize(data: dict, fallback: UsdtMethod) -> UsdtMethod:
    return UsdtMethod(
        id=fallback.id,
        label=str(data.get("label", fallback.label)).strip()[:80] or fallback.label,
        proof_label=fallback.proof_label,
        destination=str(data.get("destination", fallback.destination)).strip()[:255],
        enabled=bool(data.get("enabled", fallback.enabled)),
        description=str(data.get("description", fallback.description)).strip()[:500],
        minimum_topup_minor=max(
            10, int(data.get("minimum_topup_minor", fallback.minimum_topup_minor))
        ),
        sort_order=int(data.get("sort_order", fallback.sort_order)),
    )


async def list_payment_methods(session: AsyncSession, settings: Settings) -> tuple[UsdtMethod, ...]:
    defaults = {method.id: method for method in configured_usdt_methods(settings)}
    row = await session.get(Setting, PAYMENT_METHODS_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}
    methods = [
        _deserialize(stored.get(method_id, {}), defaults[method_id]) for method_id in _ALLOWED_IDS
    ]
    return tuple(sorted(methods, key=lambda method: (method.sort_order, method.id)))


async def get_payment_method(
    session: AsyncSession, settings: Settings, method_id: str
) -> UsdtMethod | None:
    return next(
        (
            method
            for method in await list_payment_methods(session, settings)
            if method.id == method_id
        ),
        None,
    )


async def _locked_payload(session: AsyncSession, settings: Settings) -> tuple[Setting, dict]:
    row = await session.scalar(
        select(Setting).where(Setting.key == PAYMENT_METHODS_KEY).with_for_update()
    )
    if row is None:
        payload = {method.id: _serialize(method) for method in configured_usdt_methods(settings)}
        row = Setting(key=PAYMENT_METHODS_KEY, value=payload, is_secret=False, version=1)
        session.add(row)
        await session.flush()
    payload = dict(row.value or {})
    return row, payload


async def update_payment_method(
    session: AsyncSession,
    settings: Settings,
    method_id: str,
    field: str,
    value: str | int,
) -> UsdtMethod:
    if method_id not in _ALLOWED_IDS or field not in _EDITABLE_FIELDS:
        raise ValueError("不支持的支付设置")
    row, payload = await _locked_payload(session, settings)
    defaults = {method.id: method for method in configured_usdt_methods(settings)}
    current = _deserialize(payload.get(method_id, {}), defaults[method_id])
    data = _serialize(current)
    if field == "destination":
        candidate = str(value).strip()
        if candidate and not is_valid_destination(method_id, candidate):
            raise ValueError("收款信息格式无效")
        data[field] = candidate
    elif field == "minimum_topup_minor":
        amount = int(value)
        if amount < 10:
            raise ValueError("最低充值金额不能低于 $0.10")
        data[field] = amount
    elif field == "sort_order":
        data[field] = int(value)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("内容不能为空")
        data[field] = text
    payload[method_id] = data
    row.value = payload
    row.version += 1
    await session.flush()
    return _deserialize(data, defaults[method_id])


async def toggle_payment_method(
    session: AsyncSession, settings: Settings, method_id: str
) -> UsdtMethod:
    if method_id not in _ALLOWED_IDS:
        raise ValueError("不支持的支付方式")
    row, payload = await _locked_payload(session, settings)
    defaults = {method.id: method for method in configured_usdt_methods(settings)}
    current = _deserialize(payload.get(method_id, {}), defaults[method_id])
    data = _serialize(current)
    if not current.enabled and not is_valid_destination(method_id, current.destination):
        raise ValueError("请先设置有效的收款信息")
    data["enabled"] = not current.enabled
    payload[method_id] = data
    row.value = payload
    row.version += 1
    await session.flush()
    return _deserialize(data, defaults[method_id])
