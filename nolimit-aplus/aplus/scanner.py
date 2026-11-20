"""Global market scanner. Produces timestamped, evidence-backed candidates.

A technical signal alone never authorises a trade. A candidate is graded:
  A+++              validated edge (historical + forward gates) AND risk engine approval
  A+ (paper only)   historical gates passed, forward evidence still pending, risk approved
  REJECTED          anything else, with the explicit reasons
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import pandas as pd

from .backtest import slippage_for
from .bitget.client import BitgetClient, StaleDataError
from .config import RiskLimits
from .data import INTERVAL_MS, _to_df, assert_fresh, drop_unclosed, quality_report
from .risk import AccountState, RiskEngine
from .store import Store, now_iso
from .strategies import STRATEGIES, STRATEGY_INTERVALS, generate_setups
from .universe import UniverseMember
from .validation import OOS_PASSED, REJECTED, VALIDATED

log = logging.getLogger("aplus.scanner")
SCAN_INTERVALS = ("4H", "1H")
SCAN_BARS = 1000


def load_validation(report_dir: Path) -> dict[tuple[str, str, str], dict]:
    """(symbol, strategy, interval) -> row of the latest validation run. Missing = REJECTED."""
    f = report_dir / "validation_symbols.csv"
    if not f.exists():
        return {}
    df = pd.read_csv(f).fillna("")
    return {(r.symbol, r.strategy, r.interval): r._asdict() for r in df.itertuples(index=False)}


def scan(client: BitgetClient, universe: list[UniverseMember], risk: RiskEngine, acct: AccountState,
         validation: dict, store: Store | None = None, limits: RiskLimits | None = None,
         now_ms: int | None = None) -> list[dict]:
    limits = limits or risk.limits
    out: list[dict] = []
    skipped: list[str] = []
    for m in universe:
        inst = m.instrument
        for interval in SCAN_INTERVALS:
            fetched = time.time()
            now = now_ms or int(fetched * 1000)
            try:
                raw = client.candles(inst.symbol, interval, limit=SCAN_BARS)
                candles = drop_unclosed(_to_df(raw).sort_values("ts").reset_index(drop=True), interval, now)
                q = quality_report(candles, interval)
                if not q["ok"] or q["bars"] < 400:
                    raise StaleDataError(f"quality: {q.get('reason') or 'too few bars'} ({q['bars']} bars)")
                assert_fresh(candles, interval, now, limits.max_data_age_bars)
            except Exception as exc:  # noqa: BLE001 - no verified data -> no candidate
                skipped.append(f"{inst.symbol}:{interval}: {exc}")
                continue
            last_i = len(candles) - 1
            for strat in STRATEGIES:
                if interval not in STRATEGY_INTERVALS[strat]:
                    continue
                _, setups = generate_setups(candles, strat)
                for su in (s for s in setups if s.bar_index == last_i):
                    t = client.tickers("USDT-FUTURES", inst.symbol)[0]
                    ticker_age = time.time() - float(t["ts"]) / 1000
                    v = validation.get((inst.symbol, strat, interval), {})
                    status = v.get("status", REJECTED)
                    dec = risk.evaluate(inst=inst, strategy=strat, interval=interval, side=su.side,
                                        signal_ts=su.bar_ts, entry=su.trigger, stop=su.stop, acct=acct,
                                        slippage=slippage_for(m.turnover_24h, inst.is_rwa),
                                        ticker_age_s=ticker_age, validation_status=status)
                    grade = "REJECTED"
                    if dec.approved:
                        grade = "A+++" if status == VALIDATED else "A+ (paper only)" if status == OOS_PASSED else "REJECTED"
                    bar = candles.iloc[last_i]
                    key = f"{inst.symbol}|{strat}|{interval}|{su.bar_ts}"
                    cand = {
                        "candidate_key": key, "ts": now_iso(), "symbol": inst.symbol, "strategy": strat,
                        "interval": interval, "side": su.side, "signal_ts": su.bar_ts, "grade": grade,
                        "validation_status": status, "approved": bool(dec.approved and grade != "REJECTED"),
                        "reasons": dec.reasons, "plan": dec.plan.to_dict() if dec.plan else None,
                        "evidence": {
                            "group": m.group, "asset_class": inst.asset_class,
                            "signal_bar": {"open_time_ms": int(bar.ts), "close_time_ms": int(bar.ts) + INTERVAL_MS[interval],
                                           "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume},
                            "levels": {"entry": inst.round_price(su.trigger), "stop": inst.round_price(su.stop),
                                       "tp1": inst.round_price(su.tp1), "tp2": inst.round_price(su.tp2)},
                            "fetched_at_ms": int(fetched * 1000), "ticker_ts_ms": int(t["ts"]),
                            "last_price": float(t["lastPrice"]), "ticker_age_s": round(ticker_age, 2),
                            "funding_rate": float(t.get("fundingRate") or 0),
                            "data_sha256": hashlib.sha256(candles.tail(60).to_csv(index=False).encode()).hexdigest(),
                            "data_quality": q,
                            "backtest": {k: v.get(k) for k in ("oos_trades", "oos_mean_r", "oos_win_rate",
                                                               "oos_profit_factor", "stress_mean_r", "pool_status", "reason")},
                            "expires_ms": int(bar.ts) + (1 + su.valid_bars) * INTERVAL_MS[interval],
                        },
                    }
                    out.append(cand)
                    if store:
                        store.upsert("candidates", cand)
    if store:
        store.heartbeat("scanner", "ok", {"universe": len(universe), "candidates": len(out),
                                          "approved": sum(c["approved"] for c in out), "skipped": skipped[:50]})
    log.info("scan done: %d signals, %d approved, %d skipped", len(out), sum(c["approved"] for c in out), len(skipped))
    return out
