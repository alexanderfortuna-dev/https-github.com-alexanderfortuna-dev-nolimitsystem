#!/usr/bin/env python3
"""Step 2 of the workflow, made reproducible: multi-timeframe read, strategy trigger levels, funding,
open interest, order-book depth and the OBJECTIVE part of the 100-point score, from Bitget public data.

Usage:
  python analyze_market.py SYMBOL [--style swing|day] [--side long|short] [--strategy TRX|DBO]

Output: JSON with one block per timeframe, the live setups, derivatives context and a ready-to-edit
`setup` object for evaluate_setup.py. Judgement fields that no script can know (macro event risk,
adversarial review, account state) are left for the analyst and default to the failing value.
Standard library only. Uses closed bars only.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request

import backtest_strategy as bt
from validate_market_data import fetch_bitget, validate

STYLES = {"swing": ["1D", "4H", "1H"], "day": ["4H", "1H", "15m"]}
TRIGGER_TF = {"swing": "4H", "day": "1H"}
BARS = {"1D": 420, "4H": 700, "1H": 700, "15m": 700}
GROUPS = {"crypto": "crypto", "metal": "commodity", "energy": "commodity", "commodity": "commodity",
          "stock": "equity", "etf": "equity", "index": "index_fx", "fx": "index_fx"}
REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "strategy_registry.json")


def get(path: str):
    with urllib.request.urlopen("https://api.bitget.com" + path, timeout=15) as r:
        body = json.loads(r.read())
    if body.get("code") != "00000":
        raise RuntimeError(f"{body.get('code')}: {body.get('msg')}")
    return body["data"]


def read_tf(rows: list[dict]) -> dict:
    """Objective regime read for one timeframe. Fields are null when history is too short."""
    c = [r["close"] for r in rows]
    n = len(rows)
    e20, e50, e200 = bt.ema(c, 20), bt.ema(c, 50), bt.ema(c, 200)
    a14, a100 = bt.atr(rows, 14), bt.atr(rows, 100)
    out = {"closed_bars": n, "last_bar_open_ms": int(rows[-1]["ts"]) if rows else None, "close": c[-1] if c else None}
    if n < 60:
        return dict(out, regime="unknown", note="fewer than 60 bars")
    last = n - 1
    if not math.isnan(e200[last]):
        up = e50[last] > e200[last] and c[last] > e200[last]
        dn = e50[last] < e200[last] and c[last] < e200[last]
        out["regime"], out["regime_basis"] = ("up" if up else "down" if dn else "range"), "EMA50 vs EMA200 and close vs EMA200"
        out["ema200"] = e200[last]
    else:                                # short history (new listings): fall back and say so
        out["regime"] = "up" if c[last] > e50[last] and e20[last] > e50[last] else "down" if c[last] < e50[last] and e20[last] < e50[last] else "range"
        out["regime_basis"] = f"only {n} bars: EMA20 vs EMA50 fallback (weaker evidence)"
    out.update(ema20=e20[last], ema50=e50[last], atr14=a14[last], atr_pct=round(a14[last] / c[last] * 100, 3),
               extension_atr=round((c[last] - e20[last]) / a14[last], 2),
               volatility=("expanding" if not math.isnan(a100[last]) and a14[last] > 1.25 * a100[last] else
                           "contracting" if not math.isnan(a100[last]) and a14[last] < 0.8 * a100[last] else "normal"),
               high_20=max(r["high"] for r in rows[-20:]), low_20=min(r["low"] for r in rows[-20:]),
               high_55=max(r["high"] for r in rows[-55:]) if n >= 55 else None,
               low_55=min(r["low"] for r in rows[-55:]) if n >= 55 else None)
    return out


def weekly_from_daily(rows: list[dict]) -> list[dict]:
    weeks: dict[int, dict] = {}
    for r in rows:
        k = int((r["ts"] / 86_400_000 + 3) // 7)          # weeks starting Monday (1970-01-01 was a Thursday)
        w = weeks.setdefault(k, {"ts": r["ts"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]})
        w["high"], w["low"], w["close"] = max(w["high"], r["high"]), min(w["low"], r["low"]), r["close"]
    return [weeks[k] for k in sorted(weeks)][:-1]          # drop the forming week


def live_setups(rows: list[dict], strategy: str) -> list[dict]:
    sus, a14 = bt.setups(rows, strategy)
    last = len(rows) - 1
    step = rows[-1]["ts"] - rows[-2]["ts"] if len(rows) > 1 else 0
    out = []
    for s in sus:
        if s["i"] == last:
            r = abs(s["trigger"] - s["stop"])
            out.append({"strategy": strategy, "direction": "long" if s["side"] == 1 else "short",
                        "entry_trigger": s["trigger"], "stop": s["stop"], "stop_pct": round(r / s["trigger"] * 100, 3),
                        "exit_mode": s["exit"], "reference_1r": s["trigger"] + s["side"] * r,
                        "reference_3r": s["trigger"] + s["side"] * 3 * r, "signal_bar_open_ms": int(rows[last]["ts"]),
                        "valid_for_bars": 1, "setup_expiry_ms": int(rows[last]["ts"] + 2 * step),
                        "setup_expiry_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((rows[last]["ts"] + 2 * step) / 1000))})
    return out


def objective_scores(direction: str, strategy: str, frames: dict, setup: dict | None, q: dict, contract: dict,
                     funding_ann: float | None, reg: dict, group: str) -> tuple[dict, dict]:
    side = 1 if direction == "long" else -1
    want = "up" if side == 1 else "down"
    agree = [tf for tf, f in frames.items() if f.get("regime") == want]
    sc, why = {}, {}
    sc["market_structure"] = round(15 * len(agree) / max(len(frames), 1))
    why["market_structure"] = f"{len(agree)}/{len(frames)} timeframes in a {want} regime: {agree}"
    trig_ext = abs(next(iter(frames.values())).get("extension_atr") or 0)
    if setup:
        sc["entry_quality"] = 15 if trig_ext <= 2 else 10
        why["entry_quality"] = f"objective {strategy} trigger live on the last closed bar; extension {trig_ext} ATR on the top timeframe"
    else:
        sc["entry_quality"] = 0
        why["entry_quality"] = f"no live {strategy} {direction} trigger on the last closed bar"
    status = reg.get("status", "UNKNOWN")
    if reg.get("asset_groups") and group not in reg["asset_groups"]:
        status = "REJECTED"
    status = (reg.get("direction_status") or {}).get(direction, status) if status != "REJECTED" else status
    sc["statistical_edge"] = {"VALIDATED": 20, "RESEARCH_CANDIDATE": 8}.get(status, 0)
    why["statistical_edge"] = f"registry status for {strategy} / {group} / {direction}: {status}"
    turn, spread = q.get("turnover_24h") or 0, q.get("spread_bps")
    sc["liquidity"] = (6 if turn >= 1e9 else 5 if turn >= 1e8 else 3 if turn >= 2e7 else 0) + \
                      (4 if spread is not None and spread < 1 else 3 if spread is not None and spread < 2 else 2 if spread is not None and spread < 5 else 1 if spread is not None and spread < 15 else 0)
    why["liquidity"] = f"24h turnover {turn / 1e6:.0f}M USD, spread {spread} bps"
    shock = any(f.get("volatility") == "expanding" for f in frames.values())
    sc["volatility"] = (3 if setup else 0) + (0 if shock else 2)
    why["volatility"] = "stop is 0.5-3 ATR from entry by construction" + ("; volatility expanding on at least one timeframe" if shock else "")
    if funding_ann is None:
        sc["derivatives_evidence"], why["derivatives_evidence"] = 0, "funding not available"
    else:
        carry = -side * funding_ann                        # positive = this side is paid
        sc["derivatives_evidence"] = 4 if carry > 0.05 else 3 if carry > -0.10 else 1 if carry > -0.30 else 0
        why["derivatives_evidence"] = (f"funding {funding_ann:+.1%}/yr -> carry for a {direction} {carry:+.1%}/yr. Capped at 5 of 10: "
                                       "positioning, taker flow and liquidation data need a derivatives source")
    sc["macro_event_risk"], why["macro_event_risk"] = 0, "not checked by this script: set after checking a sourced event calendar"
    if setup:
        cost = (2 * contract["taker_fee"] + (spread or 5) / 1e4 + 0.0005) / (setup["stop_pct"] / 100)
        sc["execution_cost"] = 5 if cost < 0.05 else 4 if cost < 0.08 else 3 if cost < 0.12 else 1 if cost < 0.20 else 0
        why["execution_cost"] = f"fees + spread + slippage are {cost:.1%} of the stop distance"
        pf = (reg.get("evidence") or {}).get("profit_factor")
        sc["risk_reward"] = (7 if pf and pf >= 1.25 else 4) if setup["exit_mode"] == "trail" else 8
        why["risk_reward"] = ("trailing exit: no fixed reward; scored from the measured profit factor" if setup["exit_mode"] == "trail"
                              else "fixed targets at 1R and 3R: gross 2R if both fill; confirm net value with the sizing script")
    else:
        sc["execution_cost"] = sc["risk_reward"] = 0
        why["execution_cost"] = why["risk_reward"] = "no live setup"
    return sc, why


def analyze(symbol: str, style: str = "swing", direction: str | None = None, strategy: str = "TRX") -> dict:
    snap = fetch_bitget(symbol)
    md = validate(snap)
    if md["status"] != "VERIFIED":
        return {"status": "INSUFFICIENT_DATA", "market_data": md}
    q, contract = dict(md["quotes"][0], turnover_24h=snap["quotes"][0].get("turnover_24h")), md["contract"]
    group = GROUPS.get(contract.get("asset_class") or "crypto", "crypto")
    frames, rows_by_tf, notes = {}, {}, []
    for tf in STYLES[style]:
        rows = bt.fetch_bitget(symbol, tf, BARS[tf])
        rows_by_tf[tf] = rows
        frames[tf] = read_tf(rows)
        if tf == "1D":
            wk = weekly_from_daily(rows)
            frames = {"1W (from daily)": read_tf(wk), **frames} if len(wk) >= 60 else frames
            if len(wk) < 60:
                notes.append(f"weekly read skipped: only {len(wk)} closed weeks of history on this contract")
    trig_rows = rows_by_tf[TRIGGER_TF[style]]
    setups_live = [s for st in bt.STRATEGIES for s in live_setups(trig_rows, st)] if len(trig_rows) >= 400 else []
    if len(trig_rows) < 400:
        notes.append(f"trigger timeframe has only {len(trig_rows)} bars: strategy triggers need 400")

    deriv = {}
    try:
        f = get(f"/api/v3/market/current-fund-rate?symbol={symbol}")[0]
        iv = float(f.get("fundingRateInterval") or 8)
        deriv.update(funding_rate=float(f["fundingRate"]), funding_interval_h=iv,
                     funding_annualised=round(float(f["fundingRate"]) * 24 / iv * 365, 4), next_funding_ms=int(f["nextUpdate"]))
    except Exception as exc:  # noqa: BLE001
        deriv["funding_error"] = f"{type(exc).__name__}: {exc}"
    try:
        oi = get(f"/api/v3/market/open-interest?category=USDT-FUTURES&symbol={symbol}")
        deriv["open_interest_contracts"] = float(oi["list"][0]["openInterest"])
        deriv["open_interest_usd"] = round(deriv["open_interest_contracts"] * q["price"])
        deriv["open_interest_note"] = "single snapshot: a change over time needs two readings or a derivatives data source"
    except Exception as exc:  # noqa: BLE001
        deriv["open_interest_error"] = f"{type(exc).__name__}: {exc}"
    try:
        ob = get(f"/api/v3/market/orderbook?category=USDT-FUTURES&symbol={symbol}&limit=200")
        mid = (float(ob["a"][0][0]) + float(ob["b"][0][0])) / 2
        for pct in (0.5, 1.0):
            deriv[f"bid_depth_usd_within_{pct}pct"] = round(sum(float(p) * float(s) for p, s in ob["b"] if float(p) >= mid * (1 - pct / 100)))
            deriv[f"ask_depth_usd_within_{pct}pct"] = round(sum(float(p) * float(s) for p, s in ob["a"] if float(p) <= mid * (1 + pct / 100)))
        deriv["depth_note"] = "top 200 levels only; visible depth is not guaranteed liquidity"
    except Exception as exc:  # noqa: BLE001
        deriv["orderbook_error"] = f"{type(exc).__name__}: {exc}"
    deriv["not_available_from_bitget_public_api"] = ["liquidations", "liquidation maps", "taker buy/sell imbalance", "long/short ratios"]

    try:
        registry = {s["id"]: s for s in json.load(open(REGISTRY))["strategies"]}
    except Exception:  # noqa: BLE001
        registry = {}
    regimes = [f.get("regime") for f in frames.values()]
    conflicts = []
    if "up" in regimes and "down" in regimes:
        conflicts.append("timeframes disagree: " + ", ".join(f"{tf} {f.get('regime')}" for tf, f in frames.items()))

    candidates = []
    wanted = [s for s in setups_live if (direction is None or s["direction"] == direction)]
    targets = wanted or ([{"strategy": strategy, "direction": direction, "none": True}] if direction else [])
    for s in targets:
        live = None if s.get("none") else s
        reg = registry.get(s["strategy"], {})
        sc, why = objective_scores(s["direction"], s["strategy"], frames, live, q, contract, deriv.get("funding_annualised"), reg, group)
        liq_ok = (q.get("turnover_24h") or 0) >= 2e7 and (q.get("spread_bps") or 99) < 15
        candidates.append({
            "live_trigger": live is not None,
            "setup": {
                "pair": symbol, "direction": s["direction"], "strategy": s["strategy"], "asset_group": group,
                "market_data_status": md["status"], "account_state_status": None,
                "entry": live["entry_trigger"] if live else None, "stop": live["stop"] if live else None,
                "invalidation": (f"price reaches {live['stop']} (stop), or the trigger is not hit within {live['valid_for_bars']} bar"
                                 if live else None),
                "exit_mode": live["exit_mode"] if live else None,
                "scores": sc, "score_reasons": why,
                "gates": {"data_quality_verified": True, "objective_entry_and_invalidation": live is not None,
                          "out_of_sample_evidence": False, "positive_net_expectancy": False,
                          "liquidity_sufficient": liq_ok, "portfolio_risk_acceptable": False,
                          "adversarial_review_passed": False},
                "conflicting_signals": conflicts,
            }})
    return {"status": "OK", "symbol": symbol, "style": style, "asset_group": group, "analysed_at_ms": int(time.time() * 1000),
            "quote": q, "contract": contract, "timeframes": frames,
            "live_setups": setups_live, "live_setups_note": "all directions, for context; `candidates` holds only the requested side",
            "suggested_expected_hold_h": {"swing": 120, "day": 24}[style], "derivatives": deriv,
            "conflicting_signals": conflicts, "candidates": candidates, "notes": notes,
            "how_to_use": "Pick a candidate's `setup`, fill macro_event_risk with a sourced check, set account_state_status and the "
                          "portfolio and adversarial gates from steps 4 and 6, then pass it to evaluate_setup.py. "
                          "If there is no live trigger for the requested direction there is nothing to trade: report NO TRADE."}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    opt = {argv[i][2:]: argv[i + 1] for i in range(1, len(argv) - 1) if argv[i].startswith("--")}
    if not args:
        print(__doc__)
        return 2
    try:
        res = analyze(args[0].upper(), opt.get("style", "swing"), opt.get("side"), opt.get("strategy", "TRX").upper())
    except Exception as exc:  # noqa: BLE001 - connector missing or blocked: fail closed
        res = {"status": "INSUFFICIENT_DATA", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("status") == "OK" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
