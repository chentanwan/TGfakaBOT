from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings

USDT_PRICE_PROVIDER = "usdt"


@dataclass(frozen=True, slots=True)
class UsdtMethod:
    id: str
    label: str
    proof_label: str
    destination: str
    enabled: bool

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.destination.strip())


def configured_usdt_methods(settings: Settings) -> tuple[UsdtMethod, ...]:
    return (
        UsdtMethod(
            "usdt_binance",
            "🔶 Binance Pay",
            "Binance Pay 订单号",
            settings.binance_pay_uid,
            settings.binance_pay_enabled,
        ),
        UsdtMethod(
            "usdt_bep20",
            "₮ USDT BEP20 (BSC)",
            "交易哈希",
            settings.usdt_bep20_address,
            settings.usdt_bep20_enabled,
        ),
        UsdtMethod(
            "usdt_trc20",
            "₮ USDT TRC20 (TRON)",
            "交易哈希",
            settings.usdt_trc20_address,
            settings.usdt_trc20_enabled,
        ),
        UsdtMethod(
            "usdt_arbitrum",
            "₮ USDT Arbitrum",
            "交易哈希",
            settings.usdt_arbitrum_address,
            settings.usdt_arbitrum_enabled,
        ),
    )


def available_usdt_methods(settings: Settings) -> tuple[UsdtMethod, ...]:
    return tuple(method for method in configured_usdt_methods(settings) if method.available)


def get_usdt_method(settings: Settings, method_id: str) -> UsdtMethod | None:
    return next(
        (method for method in configured_usdt_methods(settings) if method.id == method_id),
        None,
    )
