"""Reproduces reports/trxa_check.json. Rules: docs/PREREG_TRXA.md.  Run: PYTHONPATH=. python scripts/trxa_check.py"""
import itertools, json, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from aplus.backtest import CostModel, simulate, slippage_for, stats, trades_frame
from aplus.bitget.client import BitgetClient
from aplus.data import fetch_candles, funding_per_bar, quality_report
from aplus.research import load_universe
from aplus.strategies import generate_setups
from aplus.validation import week_block_bootstrap

c = BitgetClient()
u = [m for m in load_universe(c, Path("data/universe.json")) if m.group.startswith("crypto")]
ext = Path("data/candles_ext")
with ThreadPoolExecutor(4) as ex:
    data = {m.instrument.symbol: (m, df) for m, df in
            ex.map(lambda m: (m, fetch_candles(c, m.instrument.symbol, "4H", 13200, cache_dir=ext)), u)}
data = {s: v for s, v in data.items() if len(v[1]) >= 1000 and quality_report(v[1], "4H")["ok"]}
close = pd.DataFrame({s: df.set_index("ts")["close"] for s, (m, df) in data.items()}).sort_index()
r180, r540 = close / close.shift(180) - 1, close / close.shift(540) - 1
rank = r180.rank(axis=1, pct=True).where(r180.notna().sum(axis=1) >= 5)
btc_trend = generate_setups(data["BTCUSDT"][1], "TRX")[0].set_index("ts")["trend"]


def run(filtered: bool) -> pd.DataFrame:
    out = []
    for s, (m, df) in data.items():
        inst = m.instrument
        for stress in (False, True):
            cost = CostModel(inst.taker_fee, inst.maker_fee, slippage_for(m.turnover_24h, False), price_tick=inst.price_tick)
            cost = cost.stressed() if stress else cost
            fs, fu = funding_per_bar(df, pd.DataFrame(columns=["ts", "rate"]), "4H", inst.funding_interval_h)
            d, su = generate_setups(df, "TRX")
            if filtered:
                keep = []
                for x in su:
                    a, b, rk, bt = r180.at[x.bar_ts, s], r540.at[x.bar_ts, s], rank.at[x.bar_ts, s], btc_trend.get(x.bar_ts, 0)
                    if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(rk)):
                        continue
                    if (x.side == 1 and a > 0 and b > 0 and bt == 1 and rk >= 2 / 3) or \
                       (x.side == -1 and a < 0 and b < 0 and bt == -1 and rk <= 1 / 3):
                        keep.append(x)
                su = keep
            t = trades_frame(simulate(s, "4H", d, su, cost, fs, fu))
            t["stress"] = stress
            out.append(t)
    return pd.concat(out)


res = {}
for name, f in (("TRX_all_history", False), ("TRXA_aligned", True)):
    T = run(f)
    b = T[~T.stress].sort_values("entry_ts").reset_index(drop=True)
    st = stats(b.net_r.to_numpy())
    lo, hi, p = week_block_bootstrap(b)
    k = len(b) // 3
    thirds = [b.iloc[:k], b.iloc[k:2 * k], b.iloc[2 * k:]]
    weeks = (b.entry_ts.max() - b.entry_ts.min()) / 6.048e8
    streak = max((len(list(g)) for key, g in itertools.groupby(b.net_r <= 0) if key), default=0)
    res[name] = {
        "symbols": len(data),
        "period": [time.strftime("%Y-%m-%d", time.gmtime(b.entry_ts.min() / 1000)), time.strftime("%Y-%m-%d", time.gmtime(b.exit_ts.max() / 1000))],
        "trades": st["trades"], "trades_per_week": round(st["trades"] / weeks, 2), "mean_r": round(st["mean_r"], 3),
        "win_rate": round(st["win_rate"], 3), "profit_factor": round(st["profit_factor"], 2), "total_r": round(st["total_r"], 1),
        "max_dd_r": round(st["max_dd_r"], 1), "ci95": [round(lo, 3), round(hi, 3)], "p_value": round(p, 4),
        "stress_mean_r": round(float(T[T.stress].net_r.mean()), 3),
        "thirds_mean_r": [round(float(x.net_r.mean()), 3) for x in thirds], "thirds_n": [len(x) for x in thirds],
        "by_side": {("long" if s == 1 else "short"): [int(len(g)), round(float(g.net_r.mean()), 3), round(float((g.net_r > 0).mean()), 2)]
                    for s, g in b.groupby("side")},
        "share_over_3R": round(float((b.net_r > 3).mean()), 3), "best_r": round(float(b.net_r.max()), 1),
        "longest_losing_streak": int(streak),
        "by_year": {int(y): [int(len(g)), round(float(g.net_r.sum()), 1)] for y, g in b.groupby(pd.to_datetime(b.entry_ts, unit="ms").dt.year)},
    }
    if f:
        b.to_csv("reports/trxa_trades.csv", index=False)
Path("reports/trxa_check.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
