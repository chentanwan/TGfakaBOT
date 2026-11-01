from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

PRICING_SETTINGS_KEY = "plugin_prodseller_pricing"


def validate_profit_percent(value: str | Decimal) -> Decimal:
    try:
        percent = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("请输入有效的利润比例") from exc
    if not percent.is_finite() or percent < 0 or percent > 1000:
        raise ValueError("利润比例必须在 0%–1000% 之间")
    if percent.as_tuple().exponent < -2:
        raise ValueError("利润比例最多保留两位小数")
    return percent.normalize()


async def get_profit_percent(session: AsyncSession, default: Decimal) -> Decimal:
    row = await session.get(Setting, PRICING_SETTINGS_KEY)
    if row is None or not isinstance(row.value, dict):
        return validate_profit_percent(default)
    try:
        return validate_profit_percent(row.value.get("profit_percent", default))
    except ValueError:
        return validate_profit_percent(default)


async def set_profit_percent(session: AsyncSession, value: str | Decimal) -> Decimal:
    percent = validate_profit_percent(value)
    row = await session.scalar(
        select(Setting).where(Setting.key == PRICING_SETTINGS_KEY).with_for_update()
    )
    if row is None:
        row = Setting(
            key=PRICING_SETTINGS_KEY,
            value={"profit_percent": str(percent)},
            is_secret=False,
            version=1,
        )
        session.add(row)
    else:
        row.value = {**(row.value or {}), "profit_percent": str(percent)}
        row.version += 1
    await session.flush()
    return percent
