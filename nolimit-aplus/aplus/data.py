"""Market data: paged candle download, local cache, freshness and quality checks."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .bitget.client import BitgetClient, StaleDataError

log = logging.getLogger("aplus.data")

INTERVAL_MS = {"15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
COLS = ["ts", "open", "high", "low", "close", "volume", "turnover"]


def _to_df(rows: list[list[str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLS).astype({"ts": "int64"})
    df = pd.DataFrame([r[:7] for r in rows], columns=COLS)
    df["ts"] = df["ts"].astype("int64")
    for c in COLS[1:]:
        df[c] = df[c].astype(float)
    return df


def drop_unclosed(df: pd.DataFrame, interval: str, now_ms: int) -> pd.DataFrame:
    """A bar is closed only when its end time has passed. Signals never use forming bars."""
    return df[df["ts"] + INTERVAL_MS[interval] <= now_ms].reset_index(drop=True)


def fetch_candles(client: BitgetClient, symbol: str, interval: str, bars: int, *,
                  category: str = "USDT-FUTURES", cache_dir: Path | None = None,
                  now_ms: int | None = None, pause: float = 0.06) -> pd.DataFrame:
    """Return up to `bars` most recent CLOSED candles, oldest first.

    Pages backwards through /api/v3/market/history-candles (max 100 per call).
    With cache_dir, only missing recent bars are downloaded.
    """
    step = INTERVAL_MS[interval]
    now_ms = now_ms or int(time.time() * 1000)
    cache_file = cache_dir / f"{category}_{symbol}_{interval}.csv" if cache_dir else None
    cached = pd.DataFrame(columns=COLS)
    if cache_file and cache_file.exists():
        cached = pd.read_csv(cache_file)
    have_from = int(cached["ts"].min()) if len(cached) else None
    have_to = int(cached["ts"].max()) if len(cached) else None
    want_from = now_ms - bars * step

    frames = [cached]
    # 1) newest part: page back from now until we reach the cache, the wanted start, or the listing date
    end = now_ms
    exhausted = False
    while True:
        df = _to_df(client.candles(symbol, interval, category=category, limit=100, end_time=end, history=True))
        if df.empty:
            exhausted = True
            break
        frames.append(df)
        oldest = int(df["ts"].min())
        if len(df) < 100:
            exhausted = True          # reached the first bar ever listed
            break
        if oldest <= want_from or (have_to is not None and oldest <= have_to):
            break
        end = oldest                   # overlap of one bar is removed by drop_duplicates
        time.sleep(pause)
    # 2) older part: extend the cache backwards if it does not reach the wanted start
    if have_from is not None and have_from > want_from and not exhausted:
        end = have_from
        while end > want_from:
            df = _to_df(client.candles(symbol, interval, category=category, limit=100, end_time=end, history=True))
            df = df[df["ts"] < end]
            if df.empty:
                break
            frames.append(df)
            end = int(df["ts"].min())
            time.sleep(pause)

    out = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) \
        else pd.DataFrame(columns=COLS)
    if out.empty:
        return out
    out = out.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    out["ts"] = out["ts"].astype("int64")
    out = drop_unclosed(out, interval, now_ms)
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(cache_file, index=False)
    return out[out["ts"] >= want_from].reset_index(drop=True)


def fetch_funding_history(client: BitgetClient, symbol: str, *, max_pages: int = 60,
                          cache_dir: Path | None = None, pause: float = 0.06) -> pd.DataFrame:
    """Historical funding settlements (ts, rate), oldest first."""
    cache_file = cache_dir / f"funding_{symbol}.csv" if cache_dir else None
    cached = pd.read_csv(cache_file) if cache_file and cache_file.exists() else pd.DataFrame(columns=["ts", "rate"])
    newest_cached = int(cached["ts"].max()) if len(cached) else 0
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        res = client.funding_history(symbol, limit=100, cursor=page)
        if not res:
            break
        rows.extend(res)
        if min(int(r["fundingRateTimestamp"]) for r in res) <= newest_cached or len(res) < 100:
            break
        time.sleep(pause)
    new = pd.DataFrame({"ts": [int(r["fundingRateTimestamp"]) for r in rows],
                        "rate": [float(r["fundingRate"]) for r in rows]})
    out = pd.concat([cached, new], ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts")
    out = out.reset_index(drop=True)
    if cache_file and len(out):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(cache_file, index=False)
    return out


def funding_per_bar(candles: pd.DataFrame, funding: pd.DataFrame, interval: str,
                    funding_interval_h: float, assumed_abs_rate: float = 0.0001) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: (signed_rate, unknown_abs_rate).

    signed_rate  = sum of actual funding rates settled inside the bar (longs pay when > 0).
    unknown_abs  = pessimistic absolute rate charged AGAINST the position for bars
                   older than the available funding history (pro-rated by bar length).
    """
    step = INTERVAL_MS[interval]
    ts = candles["ts"].to_numpy()
    signed = np.zeros(len(ts))
    unknown = np.zeros(len(ts))
    if len(funding):
        f_ts = funding["ts"].to_numpy()
        f_rate = funding["rate"].to_numpy()
        idx = np.searchsorted(ts, f_ts, side="right") - 1      # bar containing the settlement
        ok = (idx >= 0) & (f_ts < ts[-1] + step) if len(ts) else np.zeros(0, bool)
        np.add.at(signed, idx[ok], f_rate[ok])
        first_known = f_ts.min()
    else:
        first_known = np.inf
    per_bar = assumed_abs_rate * (step / 3_600_000) / funding_interval_h
    unknown[ts < first_known] = per_bar
    return signed, unknown


def quality_report(df: pd.DataFrame, interval: str) -> dict:
    """Basic integrity checks. Failing data is never used for signals."""
    step = INTERVAL_MS[interval]
    if df.empty:
        return {"bars": 0, "ok": False, "reason": "no data"}
    ts = df["ts"].to_numpy()
    diffs = np.diff(ts)
    gaps = int((diffs > step).sum())
    missing = int(((diffs[diffs > step] // step) - 1).sum()) if gaps else 0
    bad_ohlc = int(((df["high"] < df[["open", "close", "low"]].max(axis=1)) |
                    (df["low"] > df[["open", "close", "high"]].min(axis=1)) |
                    (df[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum())
    zero_vol = int((df["volume"] <= 0).sum())
    dupes = int((diffs <= 0).sum())
    missing_pct = missing / (len(df) + missing)
    ok = bad_ohlc == 0 and dupes == 0 and missing_pct <= 0.02
    return {"bars": int(len(df)), "first_ts": int(ts[0]), "last_ts": int(ts[-1]), "gaps": gaps,
            "missing_bars": missing, "missing_pct": round(missing_pct, 5), "bad_ohlc": bad_ohlc,
            "zero_volume_bars": zero_vol, "duplicates": dupes, "ok": bool(ok),
            "reason": "" if ok else "integrity check failed"}


def assert_fresh(df: pd.DataFrame, interval: str, now_ms: int, max_age_bars: float) -> None:
    """Raise StaleDataError unless the last closed bar is recent enough to act on."""
    if df.empty:
        raise StaleDataError("no candles")
    step = INTERVAL_MS[interval]
    last_close = int(df["ts"].iloc[-1]) + step
    age_bars = (now_ms - last_close) / step
    if age_bars < 0:
        raise StaleDataError("last bar is not closed yet")
    if age_bars > max_age_bars:
        raise StaleDataError(f"last closed bar is {age_bars:.2f} bars old (limit {max_age_bars:.2f})")
