from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN


def _d(x) -> Decimal:
    return Decimal(str(x))


@dataclass(frozen=True)
class Instrument:
    """Contract specification as returned by GET /api/v3/market/instruments."""
    symbol: str
    category: str
    base: str
    quote: str
    asset_class: str          # crypto | metal | energy | stock | etf | index | fx
    is_rwa: bool
    status: str
    price_tick: float         # priceMultiplier
    qty_step: float           # quantityMultiplier
    min_qty: float
    max_qty: float
    max_market_qty: float
    min_notional: float       # minOrderAmount (USDT)
    max_leverage: float
    maker_fee: float
    taker_fee: float
    funding_interval_h: float
    launch_time_ms: int
    market_region: str | None = None

    @classmethod
    def from_api(cls, d: dict) -> "Instrument":
        f = lambda k, default=0.0: float(d.get(k) or default)  # noqa: E731
        return cls(
            symbol=d["symbol"], category=d["category"], base=d.get("baseCoin", ""), quote=d.get("quoteCoin", ""),
            asset_class=(d.get("instrumentType") or d.get("symbolType") or "crypto"),
            is_rwa=(d.get("isRwa") == "YES"), status=d.get("status", ""),
            price_tick=f("priceMultiplier") or 10 ** -int(d.get("pricePrecision") or 2),
            qty_step=f("quantityMultiplier") or 10 ** -int(d.get("quantityPrecision") or 4),
            min_qty=f("minOrderQty"), max_qty=f("maxOrderQty", math.inf) or math.inf,
            max_market_qty=f("maxMarketOrderQty", math.inf) or math.inf,
            min_notional=f("minOrderAmount", 5.0), max_leverage=f("maxLeverage", 1.0),
            maker_fee=f("makerFeeRate", 0.0002), taker_fee=f("takerFeeRate", 0.0006),
            funding_interval_h=f("fundInterval", 8.0), launch_time_ms=int(d.get("launchTime") or 0),
            market_region=d.get("marketRegion"),
        )

    # exchange-safe rounding (Decimal to avoid float artefacts like 0.30000000000000004)
    def round_qty_down(self, qty: float) -> float:
        step = _d(self.qty_step)
        return float((_d(qty) / step).to_integral_value(rounding=ROUND_DOWN) * step)

    def round_price(self, price: float) -> float:
        tick = _d(self.price_tick)
        return float((_d(price) / tick).to_integral_value(rounding=ROUND_HALF_EVEN) * tick)

    def fmt_qty(self, qty: float) -> str:
        return format(_d(self.round_qty_down(qty)).normalize(), "f")

    def fmt_price(self, price: float) -> str:
        return format(_d(self.round_price(price)).normalize(), "f")
