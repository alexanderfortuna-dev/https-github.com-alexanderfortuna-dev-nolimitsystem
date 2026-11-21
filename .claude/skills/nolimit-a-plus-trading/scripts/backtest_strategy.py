#!/usr/bin/env python3
"""Reproducible bar backtest with pessimistic fills, full costs, out-of-sample split, walk-forward,
Monte Carlo drawdown, cost sensitivity and regime breakdown. Standard library only; deterministic (seeded).

Usage:
  python backtest_strategy.py --csv candles.csv --strategy TRX [--interval-hours 4] [--taker 0.0006]
         [--maker 0.0002] [--slippage 0.0002] [--funding-8h 0.0001] [--long-only] [--seed 7]
  python backtest_strategy.py --fetch-bitget BTCUSDT --interval 4H --bars 3000 --strategy TRX

CSV columns: ts (ms, bar open time), open, high, low, close[, volume]. Bars must be CLOSED bars, oldest first.

Fill model (deliberately conservative; see references/quantitative-research.md):
  entry = stop-market at the trigger, gaps fill at the worse open, plus slippage, taker fee
  stop inside the entry bar's range = loss; stop and target in one bar = stop first
  stops gap to the worse open, with 1.5x slippage; targets need a trade-through; funding always charged
Strategies have FIXED parameters. This script does not optimise anything.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
import urllib.request

STRATEGIES = ("TRX", "DBO")
IS_FRACTION = 0.60


# ----------------------------------------------------------------------------- data
def read_csv(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({k: float(r[k]) for k in ("ts", "open", "high", "low", "close")})
    return rows


def fetch_bitget(symbol: str, interval: str, bars: int) -> list[dict]:
    step = {"5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}[interval]
    now = int(time.time() * 1000)
    end, seen = now, {}
    while len(seen) < bars:
        url = (f"https://api.bitget.com/api/v3/market/history-candles?category=USDT-FUTURES&symbol={symbol}"
               f"&interval={interval}&limit=100&endTime={end}")
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read()).get("data") or []
        if not data:
            break
        for d in data:
            seen[int(d[0])] = {"ts": float(d[0]), "open": float(d[1]), "high": float(d[2]), "low": float(d[3]), "close": float(d[4])}
        oldest = min(int(d[0]) for d in data)
        if oldest >= end:                 # no progress: reached the first listed bar
            break
        end = oldest                      # a short page is normal (each query spans at most 90 days)
        time.sleep(0.06)
    rows = [seen[k] for k in sorted(seen) if k + step <= now]      # closed bars only
    return rows[-bars:]


def check_data(rows: list[dict], step_ms: float) -> dict:
    gaps = sum(1 for a, b in zip(rows, rows[1:]) if b["ts"] - a["ts"] > step_ms)
    dup = sum(1 for a, b in zip(rows, rows[1:]) if b["ts"] <= a["ts"])
    bad = sum(1 for r in rows if not (r["low"] <= min(r["open"], r["close"]) and r["high"] >= max(r["open"], r["close"]) and r["low"] > 0))
    return {"bars": len(rows), "gaps": gaps, "duplicates_or_unsorted": dup, "bad_ohlc": bad,
            "ok": len(rows) >= 400 and dup == 0 and bad == 0 and gaps <= 0.02 * max(len(rows), 1)}


# ----------------------------------------------------------------------------- indicators (causal)
def ema(xs: list[float], n: int) -> list[float]:
    out, k, e = [math.nan] * len(xs), 2 / (n + 1), None
    for i, x in enumerate(xs):
        e = x if e is None else e + k * (x - e)
        if i >= n - 1:
            out[i] = e
    return out


def atr(rows: list[dict], n: int = 14) -> list[float]:
    out, a = [math.nan] * len(rows), None
    for i, r in enumerate(rows):
        pc = rows[i - 1]["close"] if i else r["close"]
        tr = max(r["high"] - r["low"], abs(r["high"] - pc), abs(r["low"] - pc))
        a = tr if a is None else a + (tr - a) / n
        if i >= n - 1:
            out[i] = a
    return out


def rolling(xs: list[float], n: int, fn) -> list[float]:
    return [fn(xs[i - n + 1:i + 1]) if i >= n - 1 else math.nan for i in range(len(xs))]


def setups(rows: list[dict], strategy: str, long_only: bool = False) -> tuple[list[dict], list[float]]:
    c = [r["close"] for r in rows]
    e50, e200, e300, a14, a100 = ema(c, 50), ema(c, 200), ema(c, 300), atr(rows, 14), atr(rows, 100)
    n = 55 if strategy == "TRX" else 20
    hh = rolling([r["high"] for r in rows], n, max)
    ll = rolling([r["low"] for r in rows], n, min)
    out = []
    for i in range(300, len(rows)):
        if any(math.isnan(v) for v in (e50[i], e200[i], e300[i], a14[i], a100[i], hh[i], ll[i])):
            continue
        up = e50[i] > e200[i] and c[i] > e200[i]
        dn = e50[i] < e200[i] and c[i] < e200[i]
        if strategy == "TRX":
            ok_l, ok_s = up and c[i] > e300[i], dn and c[i] < e300[i]
            extra = dict(exit="trail", trail=3.0, max_hold=180)
        else:
            ok_l = ok_s = a14[i] < a100[i]
            ok_l, ok_s = ok_l and up, ok_s and dn
            extra = dict(exit="targets", max_hold=60)
        if ok_l and c[i] < hh[i] and hh[i] - c[i] <= a14[i]:
            out.append(dict(i=i, side=1, trigger=hh[i], stop=hh[i] - 2 * a14[i], **extra))
        if ok_s and not long_only and c[i] > ll[i] and c[i] - ll[i] <= a14[i]:
            st = ll[i] + 2 * a14[i]
            if ll[i] - 3 * (st - ll[i]) > 0:
                out.append(dict(i=i, side=-1, trigger=ll[i], stop=st, **extra))
    return out, a14


# ----------------------------------------------------------------------------- simulation
def simulate(rows: list[dict], sus: list[dict], a14: list[float], taker: float, maker: float, slip: float,
             funding_per_bar: float) -> list[dict]:
    n, trades, busy = len(rows), [], -1
    for su in sus:
        i, side, trig, stop0 = su["i"], su["side"], su["trigger"], su["stop"]
        if i <= busy or i + 1 >= n:
            continue
        j = i + 1                                           # trigger is valid for the next bar only
        b = rows[j]
        if side == 1 and b["high"] >= trig:
            raw = max(trig, b["open"])
        elif side == -1 and b["low"] <= trig:
            raw = min(trig, b["open"])
        else:
            continue
        if (raw - stop0) * side <= 0:
            continue
        r_unit = abs(trig - stop0)
        entry = raw * (1 + side * slip)
        fees, pnl, fund, stop, rem, best, tp1_done, reason = entry * taker, 0.0, 0.0, stop0, 1.0, raw, False, ""
        tp1, tp2 = trig + side * r_unit, trig + side * 3 * r_unit
        e_i = j
        while True:
            b = rows[j]
            if (b["low"] <= stop) if side == 1 else (b["high"] >= stop):
                px = stop if j == e_i else (min(stop, b["open"]) if side == 1 else max(stop, b["open"]))
                px *= 1 - side * slip * 1.5
                pnl += rem * (px - entry) * side
                fees += rem * px * taker
                reason = "stop" if stop == stop0 else ("trail_stop" if su["exit"] == "trail" else "breakeven_stop")
                break
            if su["exit"] == "targets":
                if not tp1_done and ((b["high"] > tp1) if side == 1 else (b["low"] < tp1)):
                    pnl += 0.5 * (tp1 - entry) * side
                    fees += 0.5 * tp1 * maker
                    rem, tp1_done, stop = 0.5, True, trig
                    if (b["close"] - trig) * side < 0:
                        px = trig * (1 - side * slip * 1.5)
                        pnl += rem * (px - entry) * side
                        fees += rem * px * taker
                        reason = "breakeven_stop"
                        break
                if tp1_done and ((b["high"] > tp2) if side == 1 else (b["low"] < tp2)):
                    pnl += rem * (tp2 - entry) * side
                    fees += rem * tp2 * maker
                    reason = "tp2"
                    break
            fund += rem * b["close"] * funding_per_bar          # pessimistic: funding is always a cost
            if j - e_i + 1 >= su["max_hold"] or j + 1 >= n:
                px = b["close"] * (1 - side * slip)
                pnl += rem * (px - entry) * side
                fees += rem * px * taker
                reason = "time_exit" if j + 1 < n else "end_of_data"
                break
            if su["exit"] == "trail":
                best = max(best, b["high"]) if side == 1 else min(best, b["low"])
                t = best - side * su["trail"] * a14[j]
                stop = max(stop, t) if side == 1 else min(stop, t)
            j += 1
        busy = j
        if reason != "end_of_data":
            trades.append({"signal_i": i, "entry_i": e_i, "exit_i": j, "side": side, "reason": reason,
                           "gross_r": pnl / r_unit, "cost_r": (fees + fund) / r_unit,
                           "net_r": (pnl - fees - fund) / r_unit, "atr_pct": a14[i] / rows[i]["close"]})
    return trades


# ----------------------------------------------------------------------------- statistics
def summary(rs: list[float], rng: random.Random | None = None) -> dict:
    n = len(rs)
    if n == 0:
        return {"trades": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    mean = sum(rs) / n
    wr = len(wins) / n
    se = math.sqrt(wr * (1 - wr) / n)
    cum = peak = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    out = {"trades": n, "net_expectancy_r": round(mean, 4), "win_rate": round(wr, 4),
           "win_rate_ci95": [round(max(0, wr - 1.96 * se), 4), round(min(1, wr + 1.96 * se), 4)],
           "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
           "average_win_r": round(sum(wins) / len(wins), 3) if wins else None,
           "average_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
           "total_r": round(sum(rs), 2), "max_drawdown_r": round(dd, 2)}
    if rng and n >= 20:
        boots = sorted(sum(rng.choice(rs) for _ in range(n)) / n for _ in range(2000))
        out["expectancy_ci95"] = [round(boots[49], 4), round(boots[1949], 4)]
        out["p_value_expectancy_le_0"] = round((sum(1 for b in boots if b <= 0) + 1) / 2001, 4)
    return out


def monte_carlo_dd(rs: list[float], risk: float, rng: random.Random, runs: int = 2000) -> dict:
    if len(rs) < 20:
        return {"note": "fewer than 20 trades: not estimated"}
    dds = []
    for _ in range(runs):
        eq = peak = 1.0
        dd = 0.0
        for _ in range(len(rs)):
            eq *= 1 + risk * rng.choice(rs)
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak)
        dds.append(dd)
    dds.sort()
    return {"risk_per_trade": risk, "paths": runs, "median_max_drawdown": round(dds[runs // 2], 4),
            "p95_max_drawdown": round(dds[int(runs * 0.95)], 4), "worst_max_drawdown": round(dds[-1], 4)}


def run(rows: list[dict], strategy: str, taker: float, maker: float, slip: float, funding_8h: float,
        interval_h: float, long_only: bool, seed: int) -> dict:
    rng = random.Random(seed)
    fpb = funding_8h * interval_h / 8.0
    sus, a14 = setups(rows, strategy, long_only)

    def sim(mult: float = 1.0) -> list[dict]:
        return simulate(rows, sus, a14, taker * (1 + 0.25 * (mult - 1)), maker * (1 + 0.25 * (mult - 1)) if mult == 1 else taker * 1.25,
                        slip * mult, fpb)
    trades = sim()
    cut = int(len(rows) * IS_FRACTION)
    is_r = [t["net_r"] for t in trades if t["signal_i"] < cut]
    oos_r = [t["net_r"] for t in trades if t["signal_i"] >= cut]
    folds = []
    k = 5
    for f in range(k):
        lo, hi = len(rows) * f // k, len(rows) * (f + 1) // k
        rs = [t["net_r"] for t in trades if lo <= t["signal_i"] < hi]
        folds.append({"fold": f + 1, "trades": len(rs), "net_expectancy_r": round(sum(rs) / len(rs), 4) if rs else None})
    med_vol = sorted(t["atr_pct"] for t in trades)[len(trades) // 2] if trades else 0
    regimes = {
        "long": summary([t["net_r"] for t in trades if t["side"] == 1]),
        "short": summary([t["net_r"] for t in trades if t["side"] == -1]),
        "low_volatility": summary([t["net_r"] for t in trades if t["atr_pct"] <= med_vol]),
        "high_volatility": summary([t["net_r"] for t in trades if t["atr_pct"] > med_vol]),
    }
    all_r = [t["net_r"] for t in trades]
    streak = cur = 0
    for r in all_r:
        cur = cur + 1 if r <= 0 else 0
        streak = max(streak, cur)
    return {
        "strategy": strategy, "long_only": long_only, "seed": seed,
        "costs": {"taker_fee": taker, "maker_fee": maker, "slippage": slip, "funding_per_8h_always_charged": funding_8h},
        "all": summary(all_r, rng), "in_sample": summary(is_r), "out_of_sample": summary(oos_r, rng),
        "walk_forward_folds": folds,
        "positive_folds": sum(1 for f in folds if (f["net_expectancy_r"] or 0) > 0),
        "cost_sensitivity": {f"{m}x": summary([t["net_r"] for t in sim(m)]).get("net_expectancy_r") for m in (1.0, 1.5, 2.0)},
        "regimes": regimes, "longest_losing_streak": streak,
        "exit_reasons": {r: sum(1 for t in trades if t["reason"] == r) for r in sorted({t["reason"] for t in trades})},
        "monte_carlo_drawdown": monte_carlo_dd(all_r, 0.005, rng),
        "limitations": [
            "single instrument: a pooled, multi-instrument test has far more statistical power",
            "bar data cannot resolve intrabar order; pessimistic assumptions are used instead",
            "instrument chosen today (survivorship): history of delisted contracts is not included",
            "fixed-rule test; passing here is necessary, not sufficient, for VALIDATED status",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv")
    p.add_argument("--fetch-bitget")
    p.add_argument("--interval", default="4H")
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--interval-hours", type=float)
    p.add_argument("--strategy", choices=STRATEGIES, default="TRX")
    p.add_argument("--taker", type=float, default=0.0006)
    p.add_argument("--maker", type=float, default=0.0002)
    p.add_argument("--slippage", type=float, default=0.0002)
    p.add_argument("--funding-8h", type=float, default=0.0001)
    p.add_argument("--long-only", action="store_true")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)
    try:
        rows = fetch_bitget(a.fetch_bitget, a.interval, a.bars) if a.fetch_bitget else read_csv(a.csv)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "INSUFFICIENT_DATA", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    ih = a.interval_hours or {"1H": 1, "4H": 4, "1D": 24}.get(a.interval, 4)
    q = check_data(rows, ih * 3_600_000)
    if not q["ok"]:
        print(json.dumps({"status": "INSUFFICIENT_DATA", "data_quality": q,
                          "error": "need >= 400 clean, sorted, closed bars"}, indent=2))
        return 2
    rep = run(rows, a.strategy, a.taker, a.maker, a.slippage, a.funding_8h, ih, a.long_only, a.seed)
    rep.update(status="OK", data_quality=q, source=a.fetch_bitget or a.csv, bars=len(rows))
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
