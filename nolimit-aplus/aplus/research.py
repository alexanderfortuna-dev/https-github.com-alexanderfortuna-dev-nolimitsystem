"""Research pipeline: download history -> backtest -> validation report (reproducible)."""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .backtest import CostModel, simulate, slippage_for, trades_frame
from .bitget.client import BitgetClient
from .data import fetch_candles, fetch_funding_history, funding_per_bar, quality_report
from .strategies import STRATEGIES, generate_setups
from .universe import UniverseMember, build_universe

log = logging.getLogger("aplus.research")

# timeframe -> number of closed bars of history requested
HISTORY_BARS = {"4H": 6570, "1H": 8760}      # ~3 years of 4H (swing), ~1 year of 1H (day)
MIN_BARS = 1000


def sync_data(client: BitgetClient, universe: list[UniverseMember], data_dir: Path, workers: int = 4) -> dict:
    """Download/refresh candles and funding for the whole universe. Returns quality reports."""
    reports: dict[str, dict] = {}

    def one(m: UniverseMember) -> tuple[str, dict]:
        sym = m.instrument.symbol
        rep = {}
        for interval, bars in HISTORY_BARS.items():
            df = fetch_candles(client, sym, interval, bars, cache_dir=data_dir / "candles")
            rep[interval] = quality_report(df, interval)
        f = fetch_funding_history(client, sym, cache_dir=data_dir / "funding")
        rep["funding_records"] = int(len(f))
        return sym, rep

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sym, rep in ex.map(one, universe):
            reports[sym] = rep
            log.info("synced %s %s", sym, {k: (v.get("bars") if isinstance(v, dict) else v) for k, v in rep.items()})
    return reports


def run_backtests(client: BitgetClient, universe: list[UniverseMember], data_dir: Path,
                  stressed: bool = False) -> tuple[pd.DataFrame, dict]:
    """Backtest every (symbol, strategy, timeframe). Returns all trades and data-quality info."""
    all_trades: list[pd.DataFrame] = []
    quality: dict[str, dict] = {}
    for m in universe:
        inst = m.instrument
        base_cost = CostModel(taker_fee=inst.taker_fee, maker_fee=inst.maker_fee,
                              slippage=slippage_for(m.turnover_24h, inst.is_rwa), price_tick=inst.price_tick)
        cost = base_cost.stressed() if stressed else base_cost
        funding = fetch_funding_history_cached(data_dir, inst.symbol)
        for interval, bars in HISTORY_BARS.items():
            f = data_dir / "candles" / f"USDT-FUTURES_{inst.symbol}_{interval}.csv"
            if not f.exists():
                continue
            candles = pd.read_csv(f)
            q = quality_report(candles, interval)
            quality[f"{inst.symbol}:{interval}"] = q
            if not q["ok"] or q["bars"] < MIN_BARS:
                continue
            fs, fu = funding_per_bar(candles, funding, interval, inst.funding_interval_h)
            for strat in STRATEGIES:
                df, setups = generate_setups(candles, strat)
                tr = trades_frame(simulate(inst.symbol, interval, df, setups, cost, fs, fu))
                if len(tr):
                    tr["group"] = m.group
                    tr["first_bar_ts"] = int(candles["ts"].iloc[0])
                    tr["last_bar_ts"] = int(candles["ts"].iloc[-1])
                    all_trades.append(tr)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return trades, quality


def fetch_funding_history_cached(data_dir: Path, symbol: str) -> pd.DataFrame:
    f = data_dir / "funding" / f"funding_{symbol}.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["ts", "rate"])


def universe_snapshot(universe: list[UniverseMember]) -> list[dict]:
    return [{"symbol": m.instrument.symbol, "group": m.group, "asset_class": m.instrument.asset_class,
             "is_rwa": m.instrument.is_rwa, "turnover_24h": m.turnover_24h, "last_price": m.last_price,
             "max_leverage": m.instrument.max_leverage, "taker_fee": m.instrument.taker_fee,
             "maker_fee": m.instrument.maker_fee, "funding_interval_h": m.instrument.funding_interval_h,
             "launch_time_ms": m.instrument.launch_time_ms} for m in universe]


def save_universe(universe: list[UniverseMember], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at_ms": int(time.time() * 1000), "members": universe_snapshot(universe)},
                               indent=1))


def load_universe(client: BitgetClient, path: Path) -> list[UniverseMember]:
    """Rebuild UniverseMember objects for a saved universe (fresh contract specs, saved liquidity tiers)."""
    from .bitget.models import Instrument
    from .universe import CATEGORY
    saved = {m["symbol"]: m for m in json.loads(path.read_text())["members"]}
    insts = {d["symbol"]: Instrument.from_api(d) for d in client.instruments(CATEGORY)}
    return [UniverseMember(insts[s], m["group"], m["turnover_24h"], m["last_price"], 0.0)
            for s, m in saved.items() if s in insts]


def run_validation(client: BitgetClient, data_dir: Path, report_dir: Path) -> dict:
    """Full reproducible run: backtest (base + stressed costs) -> pool and symbol validation -> report files."""
    from .validation import GATES, apply_forward_gate, validate_pools, validate_symbols
    universe = load_universe(client, data_dir / "universe.json")
    trades, quality = run_backtests(client, universe, data_dir, stressed=False)
    stressed, _ = run_backtests(client, universe, data_dir, stressed=True)
    pools = validate_pools(trades, stressed)
    symbols = validate_symbols(trades, stressed, pools)
    # ---- forward (shadow) evidence: only signals generated AFTER the rules were frozen count.
    # The freeze timestamp is written once and never moved, so forward results cannot be cherry-picked.
    freeze_file = report_dir / "validation_freeze.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    if not freeze_file.exists():
        freeze_file.write_text(json.dumps({"frozen_at_ms": int(time.time() * 1000),
                                           "note": "strategy rules and gates frozen; forward evidence starts here"}))
    frozen_at = json.loads(freeze_file.read_text())["frozen_at_ms"]
    fwd = trades[trades["signal_ts"] >= frozen_at] if len(trades) else trades
    forward = {}
    for p in pools:
        f = fwd[(fwd["strategy"] == p.strategy) & (fwd["interval"] == p.interval) & (fwd["group"] == p.group)] if len(fwd) else fwd
        r = f["net_r"].tolist() if len(f) else []
        forward[f"{p.strategy}|{p.interval}|{p.group}"] = {"trades": len(r), "mean_r": float(sum(r) / len(r)) if r else None}
        promoted = apply_forward_gate(p.status, r)
        if promoted != p.status:
            symbols.loc[(symbols["strategy"] == p.strategy) & (symbols["interval"] == p.interval) &
                        (symbols["group"] == p.group) & (symbols["status"] == p.status), "status"] = promoted
            p.status = promoted
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    trades.to_csv(report_dir / "backtest_trades.csv.gz", index=False)
    symbols.to_csv(report_dir / "validation_symbols.csv", index=False)
    summary = {
        "run_id": run_id, "generated_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "universe_symbols": len(universe), "history_bars": HISTORY_BARS, "gates": GATES,
        "total_trades": int(len(trades)),
        "data_quality_failed": {k: v for k, v in quality.items() if not v["ok"]},
        "data_too_short": [k for k, v in quality.items() if v["ok"] and v["bars"] < MIN_BARS],
        "frozen_at_ms": frozen_at, "forward_shadow": forward,
        "pools": [p.to_dict() for p in pools],
        "pools_passed": sum(p.status != "REJECTED" for p in pools),
        "symbols_passed": int((symbols["status"] != "REJECTED").sum()) if len(symbols) else 0,
    }
    (report_dir / "validation_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return {"summary": summary, "pools": pools, "symbols": symbols, "trades": trades}
