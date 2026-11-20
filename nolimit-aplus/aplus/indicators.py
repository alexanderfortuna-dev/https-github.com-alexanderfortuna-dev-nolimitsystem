from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # Wilder


def donchian_high(df: pd.DataFrame, n: int) -> pd.Series:
    """Highest high of the previous n bars INCLUDING the current closed bar."""
    return df["high"].rolling(n, min_periods=n).max()


def donchian_low(df: pd.DataFrame, n: int) -> pd.Series:
    return df["low"].rolling(n, min_periods=n).min()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """All indicators at row i use data up to and including bar i only (no look-ahead)."""
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["atr14"] = atr(out, 14)
    out["atr100"] = atr(out, 100)
    out["dc_high20"] = donchian_high(out, 20)
    out["dc_low20"] = donchian_low(out, 20)
    out["ll3"] = out["low"].rolling(3, min_periods=3).min()
    out["hh3"] = out["high"].rolling(3, min_periods=3).max()
    out["trend"] = np.where((out["ema50"] > out["ema200"]) & (out["close"] > out["ema200"]), 1,
                            np.where((out["ema50"] < out["ema200"]) & (out["close"] < out["ema200"]), -1, 0))
    return out
