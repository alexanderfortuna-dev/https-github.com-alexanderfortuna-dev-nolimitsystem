"""Rule-based setups. Parameters are fixed a priori (no optimisation on test data).

Every setup is expressed as a pending TRIGGER order created at the close of bar i:
    side, trigger (stop-entry price), stop (protective stop), valid for `valid_bars` bars.
Targets are derived from planned risk R = |trigger - stop|:
    TP1 = 1R on 50% of the position (then stop -> entry), TP2 = 3R on the remainder.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import add_indicators

TP1_R = 1.0
TP2_R = 3.0
TP1_FRACTION = 0.5
MAX_HOLD_BARS = 60
WARMUP_BARS = 300


@dataclass(frozen=True)
class Setup:
    strategy: str
    bar_index: int       # index of the closed signal bar
    bar_ts: int          # open time (ms) of the signal bar
    side: int            # +1 long, -1 short
    trigger: float
    stop: float
    valid_bars: int
    exit_mode: str = "targets"      # "targets" (TP1/TP2) or "trail" (chandelier trailing stop, no fixed target)
    trail_mult: float = 0.0         # ATR multiple for the trailing stop
    max_hold: int = MAX_HOLD_BARS

    @property
    def risk_per_unit(self) -> float:
        return abs(self.trigger - self.stop)

    @property
    def tp1(self) -> float:
        return self.trigger + self.side * TP1_R * self.risk_per_unit

    @property
    def tp2(self) -> float:
        return self.trigger + self.side * TP2_R * self.risk_per_unit


def _mk(strategy: str, df: pd.DataFrame, mask: np.ndarray, side: int, trigger: np.ndarray, stop: np.ndarray,
        valid_bars: int, **kw) -> list[Setup]:
    atr = df["atr14"].to_numpy()
    out = []
    for i in np.flatnonzero(mask):
        if i < WARMUP_BARS:
            continue
        t, s, a = float(trigger[i]), float(stop[i]), float(atr[i])
        if not (np.isfinite(t) and np.isfinite(s) and np.isfinite(a)) or a <= 0:
            continue
        dist = (t - s) * side
        if dist < 0.5 * a or dist > 3.0 * a:      # reject stops that are too tight (noise) or too wide
            continue
        if t - side * TP2_R * dist <= 0 or dist / t > 0.25:   # target must be a real price; stop never wider than 25%
            continue
        out.append(Setup(strategy, int(i), int(df["ts"].iloc[i]), side, t, s, valid_bars, **kw))
    return out


def trend_pullback(df: pd.DataFrame) -> list[Setup]:
    """TPB: in an established trend, price dips into the EMA20 and closes back in trend direction.
    Entry on a break of the reclaim bar's extreme; stop beyond the 3-bar swing."""
    c, o, h, l = (df[k].to_numpy() for k in ("close", "open", "high", "low"))
    ema20, atr, trend = df["ema20"].to_numpy(), df["atr14"].to_numpy(), df["trend"].to_numpy()
    prev_low = np.roll(l, 1); prev_high = np.roll(h, 1); prev_ema = np.roll(ema20, 1)
    touched_l = (np.minimum(l, prev_low) <= np.maximum(ema20, prev_ema))
    touched_s = (np.maximum(h, prev_high) >= np.minimum(ema20, prev_ema))
    long_m = (trend == 1) & touched_l & (c > ema20) & (c > o)
    short_m = (trend == -1) & touched_s & (c < ema20) & (c < o)
    long_m[0] = short_m[0] = False
    longs = _mk("TPB", df, long_m, 1, h, df["ll3"].to_numpy() - 0.25 * atr, 2)
    shorts = _mk("TPB", df, short_m, -1, l, df["hh3"].to_numpy() + 0.25 * atr, 2)
    return sorted(longs + shorts, key=lambda s: s.bar_index)


def donchian_breakout(df: pd.DataFrame) -> list[Setup]:
    """DBO: break of the 20-bar range in trend direction after volatility contraction.
    Stop-entry at the range extreme, stop 2 ATR away."""
    c = df["close"].to_numpy()
    atr, atr100, trend = df["atr14"].to_numpy(), df["atr100"].to_numpy(), df["trend"].to_numpy()
    dch, dcl = df["dc_high20"].to_numpy(), df["dc_low20"].to_numpy()
    squeeze = atr < atr100
    long_m = (trend == 1) & squeeze & (c < dch) & (dch - c <= 1.0 * atr)
    short_m = (trend == -1) & squeeze & (c > dcl) & (c - dcl <= 1.0 * atr)
    longs = _mk("DBO", df, long_m, 1, dch, dch - 2.0 * atr, 1)
    shorts = _mk("DBO", df, short_m, -1, dcl, dcl + 2.0 * atr, 1)
    return sorted(longs + shorts, key=lambda s: s.bar_index)


def trend_runner(df: pd.DataFrame) -> list[Setup]:
    """TRX: few trades, large winners. 55-bar breakout with the 4H trend and the ~50-day average,
    2 ATR initial stop, 3 ATR chandelier trailing stop, no fixed target. Rules pre-registered in
    docs/PREREG_TRX.md before the first test."""
    c = df["close"].to_numpy()
    atr, trend, ema300 = df["atr14"].to_numpy(), df["trend"].to_numpy(), df["ema300"].to_numpy()
    dch, dcl = df["dc_high55"].to_numpy(), df["dc_low55"].to_numpy()
    long_m = (trend == 1) & (c > ema300) & (c < dch) & (dch - c <= 1.0 * atr)
    short_m = (trend == -1) & (c < ema300) & (c > dcl) & (c - dcl <= 1.0 * atr)
    kw = dict(exit_mode="trail", trail_mult=3.0, max_hold=180)
    longs = _mk("TRX", df, long_m, 1, dch, dch - 2.0 * atr, 1, **kw)
    shorts = _mk("TRX", df, short_m, -1, dcl, dcl + 2.0 * atr, 1, **kw)
    return sorted(longs + shorts, key=lambda s: s.bar_index)


def capitulation_reversal(df: pd.DataFrame) -> list[Setup]:
    """CAP: counter-trend reversal after a 3-ATR extension from the EMA20. Pre-registered in docs/PREREG_CAP.md."""
    c, o, h, l = (df[k].to_numpy() for k in ("close", "open", "high", "low"))
    ema20, atr = df["ema20"].to_numpy(), df["atr14"].to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        ext_lo = pd.Series((l - ema20) / atr).rolling(3, min_periods=3).min().to_numpy()
        ext_hi = pd.Series((h - ema20) / atr).rolling(3, min_periods=3).max().to_numpy()
    pc = np.roll(c, 1)
    long_m = (ext_lo <= -3.0) & (c > o) & (c > pc)
    short_m = (ext_hi >= 3.0) & (c < o) & (c < pc)
    long_m[0] = short_m[0] = False
    longs = _mk("CAP", df, long_m, 1, h, df["ll3"].to_numpy() - 0.25 * atr, 1)
    shorts = _mk("CAP", df, short_m, -1, l, df["hh3"].to_numpy() + 0.25 * atr, 1)
    return sorted(longs + shorts, key=lambda s: s.bar_index)


STRATEGIES = {"TPB": trend_pullback, "DBO": donchian_breakout, "TRX": trend_runner, "CAP": capitulation_reversal}
STRATEGY_INTERVALS = {"TPB": ("4H", "1H"), "DBO": ("4H", "1H"), "TRX": ("4H",), "CAP": ("4H",)}


def generate_setups(candles: pd.DataFrame, strategy: str) -> tuple[pd.DataFrame, list[Setup]]:
    df = add_indicators(candles)
    return df, STRATEGIES[strategy](df)
