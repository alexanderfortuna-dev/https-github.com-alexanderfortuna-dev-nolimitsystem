#!/usr/bin/env python3
"""Portfolio risk gate. Decides whether ANY new position may be opened, from verified account state.

Usage: python evaluate_portfolio_risk.py account.json

account.json:
{
  "snapshot_ts_ms": 1795214000000, "now_ms": 1795214060000, "source": "bitget-api-readonly" | "screenshots",
  "complete": true,                         # false if any account view failed to load
  "ledgers": {"unified": 6000, "funding": 1000, "bots": 2400, "cfd": 250},   # each ledger counted ONCE
  "reported_overview_total": 9650,          # optional cross-check (exchange "Overview")
  "liabilities": 0,
  "trading_equity": 6000,                   # equity of the account the trade would be placed in
  "day_start_equity": 6050, "week_start_equity": 6200,
  "positions": [{"symbol": "BTCUSDT", "side": "long", "notional": 3000, "asset_group": "crypto",
                 "managed": false, "planned_risk": 0, "margin_mode": "crossed", "leverage_setting": 20}],
  "pending_orders": [{"symbol": "SUIUSDT", "side": "buy", "notional": 60, "reduce_only": false}],
  "candidate": {"symbol": "ETHUSDT", "side": "short", "asset_group": "crypto", "notional": 400, "planned_risk": 30},
  "kill_switch": false,
  "limits": {}
}
"""
from __future__ import annotations

import sys
import time

from _common import emit, load_input, num, tightened

MAX_SNAPSHOT_AGE_S = {"bitget-api-readonly": 120, "screenshots": 300}
OVERVIEW_TOLERANCE = 0.03
MAX_CLUSTER_RISK_SHARE = 2 / 3      # of the open-risk cap in one correlated group + direction


def evaluate(a: dict) -> dict:
    L = tightened(a.get("limits"))
    reasons, warnings = [], []
    now_ms = int(a.get("now_ms") or time.time() * 1000)

    if a.get("kill_switch"):
        reasons.append("kill_switch_active")
    if a.get("complete") is not True:
        reasons.append("account_incomplete: at least one account view is missing; no new entries")
    ts = num(a, "snapshot_ts_ms")
    limit = MAX_SNAPSHOT_AGE_S.get(a.get("source"), 120)
    if ts is None:
        reasons.append("account_snapshot_has_no_timestamp")
    elif (now_ms - ts) / 1000 > limit:
        reasons.append(f"account_snapshot_stale: {(now_ms - ts) / 1000:.0f}s old, limit {limit}s for {a.get('source')}")

    ledgers = a.get("ledgers") or {}
    known = {k: float(v) for k, v in ledgers.items() if isinstance(v, (int, float))}
    unread = [k for k, v in ledgers.items() if v is None]
    total = sum(known.values())
    if unread:
        warnings.append(f"ledgers not readable (excluded, not assumed zero): {', '.join(unread)}")
    overview = num(a, "reported_overview_total")
    cross = None
    if overview and total:
        cross = (total - overview) / overview
        if abs(cross) > OVERVIEW_TOLERANCE:
            reasons.append(f"equity_cross_check_failed: ledgers {total:.2f} vs overview {overview:.2f} ({cross:+.1%}); "
                           "possible double counting or a missing ledger")
    equity = num(a, "trading_equity")
    if equity is None or equity <= 0:
        reasons.append("trading_equity_missing_or_not_positive")
        equity = 0.0

    positions = a.get("positions") or []
    gross = sum(abs(float(p.get("notional") or 0)) for p in positions)
    open_risk = sum(float(p.get("planned_risk") or 0) for p in positions if p.get("managed"))
    unmanaged_no_stop = [p["symbol"] for p in positions if not p.get("managed") and not p.get("has_stop")]
    if unmanaged_no_stop:
        warnings.append("existing positions without a known stop (never modified by this skill): " + ", ".join(unmanaged_no_stop))
    entry_orders = [o for o in a.get("pending_orders") or [] if not o.get("reduce_only")]
    pending_notional = sum(abs(float(o.get("notional") or 0)) for o in entry_orders)

    for label, start, lim in (("daily", num(a, "day_start_equity"), L["daily_loss_limit"]),
                              ("weekly", num(a, "week_start_equity"), L["weekly_loss_limit"])):
        if start and equity:
            chg = (equity - start) / start
            if chg <= -lim:
                reasons.append(f"{label}_loss_circuit_breaker: {chg:.2%} <= -{lim:.2%}")
        elif not start:
            warnings.append(f"{label} start equity unknown: {label} circuit breaker could not be evaluated")

    cand = a.get("candidate")
    after_gross, after_risk, cluster = gross + pending_notional, open_risk, None
    if cand:
        sym, cside = cand.get("symbol"), str(cand.get("side", "")).lower()
        if any(p.get("symbol") == sym for p in positions):
            reasons.append(f"existing_position_on_symbol: {sym} (no adding, no averaging down, no hedging the owner's position)")
        if any(o.get("symbol") == sym for o in entry_orders):
            reasons.append(f"pending_entry_order_on_symbol: {sym}")
        after_gross += abs(float(cand.get("notional") or 0))
        after_risk += float(cand.get("planned_risk") or 0)
        grp = cand.get("asset_group")
        same = [p for p in positions if p.get("asset_group") == grp and str(p.get("side", "")).lower() == cside]
        cluster_risk = sum(float(p.get("planned_risk") or 0) for p in same if p.get("managed")) + float(cand.get("planned_risk") or 0)
        cluster = {"group": grp, "side": cside, "positions": [p["symbol"] for p in same], "planned_risk_after": cluster_risk}
        if equity and cluster_risk > MAX_CLUSTER_RISK_SHARE * L["max_open_risk"] * equity + 1e-9:
            reasons.append(f"concentration: {len(same) + 1} same-direction {grp} positions would carry {cluster_risk:.2f} "
                           "of planned risk; treat them as one bet")
        if equity and float(cand.get("planned_risk") or 0) > L["risk_per_trade"] * equity + 1e-9:
            reasons.append("candidate_risk_exceeds_per_trade_limit")
    if equity:
        if after_gross / equity > L["max_gross_exposure"] + 1e-9:
            reasons.append(f"gross_exposure_cap: {after_gross / equity:.2f}x > {L['max_gross_exposure']}x "
                           "(includes existing positions and pending entry orders)")
        if after_risk > L["max_open_risk"] * equity + 1e-9:
            reasons.append(f"open_risk_cap: {after_risk:.2f} > {L['max_open_risk'] * equity:.2f}")

    return {
        "can_open_new_position": not reasons, "reasons": reasons, "warnings": warnings,
        "total_equity_all_ledgers": round(total, 2), "ledgers": ledgers, "overview_cross_check": cross,
        "trading_equity": equity, "liabilities": num(a, "liabilities") or 0.0,
        "gross_exposure": round(gross, 2), "effective_leverage": round(gross / equity, 4) if equity else None,
        "gross_exposure_after": round(after_gross, 2),
        "portfolio_leverage_after": round(after_gross / equity, 4) if equity else None,
        "open_planned_risk": round(open_risk, 2), "open_planned_risk_after": round(after_risk, 2),
        "cluster": cluster, "limits": L,
        "existing_positions_modified": False,
    }


if __name__ == "__main__":
    res = evaluate(load_input(sys.argv))
    sys.exit(emit(res, res["can_open_new_position"]))
