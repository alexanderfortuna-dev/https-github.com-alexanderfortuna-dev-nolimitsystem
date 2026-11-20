"""Bar-level event backtester with pessimistic fill assumptions and full costs.

Fill model (deliberately conservative):
  * Entry: stop-market trigger. Fills at max(trigger, open) for longs (gap-through
    fills at the worse open), plus slippage. Taker fee.
  * If the stop is also inside the ENTRY bar's range the trade is assumed stopped
    in that bar (intrabar order is unknowable, so we take the loss).
  * In any later bar where both stop and target are touched, the STOP is assumed first.
  * Stop: fills at min(stop, open) for longs (gaps hurt), plus 1.5x slippage. Taker fee.
  * Targets: limit orders; require price to trade THROUGH the level by one tick. Maker fee.
  * Funding: actual historical settlements where available; where history is missing
    a pessimistic absolute rate is charged against the position.
  * One position at a time per (symbol, strategy). No pyramiding, no averaging down.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .strategies import Setup, TP1_FRACTION, MAX_HOLD_BARS


@dataclass(frozen=True)
class CostModel:
    taker_fee: float = 0.0006
    maker_fee: float = 0.0002
    slippage: float = 0.0002          # fraction of price on market fills
    stop_slippage_mult: float = 1.5
    price_tick: float = 0.0

    def stressed(self, mult: float = 2.0) -> "CostModel":
        return CostModel(self.taker_fee * 1.25, self.taker_fee * 1.25, self.slippage * mult,
                         self.stop_slippage_mult, self.price_tick)


@dataclass
class Trade:
    symbol: str
    strategy: str
    interval: str
    side: int
    signal_ts: int
    entry_ts: int
    exit_ts: int
    trigger: float
    stop: float
    entry_fill: float
    exit_reason: str
    bars_held: int
    gross_r: float
    fees_r: float
    funding_r: float
    net_r: float

    def to_dict(self) -> dict:
        return asdict(self)


def slippage_for(turnover_24h_usd: float, is_rwa: bool) -> float:
    """Liquidity-tiered slippage assumption (fraction of price) for market/stop fills."""
    if turnover_24h_usd >= 1e9:
        s = 0.0001
    elif turnover_24h_usd >= 1e8:
        s = 0.0002
    elif turnover_24h_usd >= 2e7:
        s = 0.0004
    else:
        s = 0.0008
    return s + (0.0002 if is_rwa else 0.0)


def simulate(symbol: str, interval: str, df: pd.DataFrame, setups: list[Setup], cost: CostModel,
             funding_signed: np.ndarray | None = None, funding_unknown: np.ndarray | None = None) -> list[Trade]:
    o, h, l, c, ts = (df[k].to_numpy() for k in ("open", "high", "low", "close", "ts"))
    n = len(df)
    fs = funding_signed if funding_signed is not None else np.zeros(n)
    fu = funding_unknown if funding_unknown is not None else np.zeros(n)
    tick = cost.price_tick
    trades: list[Trade] = []
    busy_until = -1                       # index of the bar in which the open trade exits

    for su in setups:
        i = su.bar_index
        if i <= busy_until or i + 1 >= n:
            continue
        side, trig, stop0 = su.side, su.trigger, su.stop
        r_unit = abs(trig - stop0)
        if r_unit <= 0:
            continue
        # ---- entry search
        entry_i = -1
        for j in range(i + 1, min(i + 1 + su.valid_bars, n)):
            if side == 1 and h[j] >= trig:
                raw = max(trig, o[j]); entry_i = j; break
            if side == -1 and l[j] <= trig:
                raw = min(trig, o[j]); entry_i = j; break
        if entry_i < 0:
            continue
        if (raw - stop0) * side <= 0:     # gapped beyond the stop: order would be rejected/meaningless
            continue
        entry = raw * (1 + side * cost.slippage)
        tp1, tp2 = su.tp1, su.tp2

        remaining = 1.0                   # fraction of position still open
        stop = stop0
        pnl = 0.0                         # price pnl per initial unit, in price units
        fees = entry * cost.taker_fee     # per initial unit, price units
        funding = 0.0
        reason = ""
        exit_i = entry_i
        tp1_done = False

        def stop_fill(j: int, level: float) -> float:
            px = min(level, o[j]) if side == 1 else max(level, o[j])
            if j == entry_i:              # stop cannot gap on the entry bar: it did not exist at the open
                px = level
            return px * (1 - side * cost.slippage * cost.stop_slippage_mult)

        j = entry_i
        while True:
            hit_stop = (l[j] <= stop) if side == 1 else (h[j] >= stop)
            if hit_stop:
                px = stop_fill(j, stop)
                pnl += remaining * (px - entry) * side
                fees += remaining * px * cost.taker_fee
                reason = "stop" if not tp1_done else "breakeven_stop"
                remaining = 0.0
                exit_i = j
                break
            if not tp1_done and ((h[j] > tp1 + tick) if side == 1 else (l[j] < tp1 - tick)):
                pnl += TP1_FRACTION * (tp1 - entry) * side
                fees += TP1_FRACTION * tp1 * cost.maker_fee
                remaining -= TP1_FRACTION
                tp1_done = True
                stop = trig               # protect: move stop to the planned entry
                if (c[j] - trig) * side < 0:
                    # bar closed back through the entry after TP1: the breakeven stop must have filled
                    px = trig * (1 - side * cost.slippage * cost.stop_slippage_mult)
                    pnl += remaining * (px - entry) * side
                    fees += remaining * px * cost.taker_fee
                    remaining = 0.0
                    reason = "breakeven_stop"
                    exit_i = j
                    break
            if tp1_done and ((h[j] > tp2 + tick) if side == 1 else (l[j] < tp2 - tick)):
                pnl += remaining * (tp2 - entry) * side
                fees += remaining * tp2 * cost.maker_fee
                remaining = 0.0
                reason = "tp2"
                exit_i = j
                break
            # funding accrues on what is still open at the end of the bar
            funding += remaining * c[j] * (side * fs[j] + fu[j])
            if j - entry_i + 1 >= MAX_HOLD_BARS or j + 1 >= n:
                px = c[j] * (1 - side * cost.slippage)
                pnl += remaining * (px - entry) * side
                fees += remaining * px * cost.taker_fee
                remaining = 0.0
                reason = "time_exit" if j + 1 < n else "end_of_data"
                exit_i = j
                break
            j += 1

        busy_until = exit_i
        if reason == "end_of_data":
            continue                      # incomplete trade: excluded from statistics
        trades.append(Trade(symbol, su.strategy, interval, side, int(ts[i]), int(ts[entry_i]), int(ts[exit_i]),
                            trig, stop0, entry, reason, exit_i - entry_i + 1,
                            pnl / r_unit, fees / r_unit, funding / r_unit, (pnl - fees - funding) / r_unit))
    return trades


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    cols = list(Trade.__dataclass_fields__)
    return pd.DataFrame([t.to_dict() for t in trades], columns=cols)


def stats(net_r: np.ndarray, risk_fraction: float = 0.005) -> dict:
    """Summary of a sequence of net R results (chronological)."""
    r = np.asarray(net_r, dtype=float)
    n = len(r)
    if n == 0:
        return {"trades": 0, "mean_r": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "total_r": 0.0,
                "max_dd_r": 0.0, "max_dd_pct": 0.0, "compounded_return_pct": 0.0, "t_stat": 0.0}
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    cum = np.cumsum(r)
    dd_r = float((np.maximum.accumulate(np.concatenate([[0.0], cum])) - np.concatenate([[0.0], cum])).max())
    eq = np.cumprod(1 + risk_fraction * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    dd_pct = float(((peak - np.concatenate([[1.0], eq])) / peak).max())
    sd = r.std(ddof=1) if n > 1 else 0.0
    return {"trades": n, "mean_r": float(r.mean()), "win_rate": float((r > 0).mean()),
            "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
            "total_r": float(r.sum()), "max_dd_r": dd_r, "max_dd_pct": dd_pct * 100,
            "compounded_return_pct": float((eq[-1] - 1) * 100),
            "t_stat": float(r.mean() / (sd / np.sqrt(n))) if sd > 0 else 0.0}
