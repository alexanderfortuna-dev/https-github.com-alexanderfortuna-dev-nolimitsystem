#!/usr/bin/env python3
"""Deterministic position sizing. Size comes from the STOP distance and account risk; the leverage
setting is derived afterwards and can never increase the amount at risk.

Usage: python calculate_position_size.py request.json     (or JSON on stdin)

request.json:
{
  "equity": 10000, "available": 10000,            # VERIFIED account numbers in quote currency (USDT)
  "side": "long" | "short", "entry": 60000, "stop": 59400,
  "contract": {"symbol": "BTCUSDT", "price_tick": 0.1, "qty_step": 0.0001, "min_qty": 0.0001,
               "min_notional": 5, "max_qty": 1200, "max_leverage": 150, "taker_fee": 0.0006, "maker_fee": 0.0002},
  "slippage": 0.0002,                              # fraction of price on market/stop fills
  "existing_gross_exposure": 0, "open_planned_risk": 0,
  "funding_rate_per_interval": 0.0001, "funding_interval_h": 8, "expected_hold_h": 48,   # optional
  "tp1_r": 1.0, "tp2_r": 3.0, "tp1_fraction": 0.5,                                      # optional
  "exit_mode": "targets" | "trail", "trail_rule": "3 ATR below the highest high since entry",  # optional
  "equity_source": "verified" | "user_stated",    # user_stated -> plan is INDICATIVE, never executable
  "requested_leverage": 50,                        # optional; honoured only if it is SAFER than the computed one
  "quote_to_account_rate": 1.0,                    # optional currency conversion (quote ccy -> account ccy)
  "limits": {"risk_per_trade": 0.005}              # optional; can only tighten
}
"""
from __future__ import annotations

import math
import sys

from _common import emit, load_input, num, round_down, round_price, tightened

ASSUMED_MMR = 0.02           # conservative maintenance-margin rate when the venue's tiers are unknown
LIQ_BUFFER_MULT = 2.0        # liquidation must sit at least this many stop-distances from entry
MAX_LEVERAGE_SETTING = 20
MAX_MARGIN_SHARE = 0.5


def size(req: dict) -> dict:
    L = tightened(req.get("limits"))
    missing = [k for k in ("equity", "available", "entry", "stop") if num(req, k) is None]
    c = req.get("contract") or {}
    missing += [f"contract.{k}" for k in ("price_tick", "qty_step", "min_qty", "min_notional", "taker_fee") if num(c, k) is None]
    side = {"long": 1, "short": -1}.get(str(req.get("side", "")).lower())
    if side is None:
        missing.append("side")
    if missing:
        return {"approved": False, "reasons": ["missing_input: " + ", ".join(missing)]}
    fx = num(req, "quote_to_account_rate") or 1.0
    equity, available = num(req, "equity"), num(req, "available")
    entry, stop = num(req, "entry"), num(req, "stop")
    slip = num(req, "slippage") if num(req, "slippage") is not None else 0.0005
    taker, maker = num(c, "taker_fee"), (num(c, "maker_fee") if num(c, "maker_fee") is not None else num(c, "taker_fee"))
    reasons = []
    if equity <= 0:
        reasons.append("equity_not_positive")
    if entry <= 0 or stop <= 0:
        reasons.append("invalid_prices")
    elif (entry - stop) * side <= 0:
        reasons.append("stop_on_wrong_side")
    if reasons:
        return {"approved": False, "reasons": reasons}

    open_risk = num(req, "open_planned_risk") or 0.0
    gross = num(req, "existing_gross_exposure") or 0.0
    budget = L["risk_per_trade"] * equity
    if L["max_open_risk"] * equity - open_risk < budget * 0.999:
        return {"approved": False, "reasons": [f"open_risk_cap: {open_risk:.2f} already planned, cap {L['max_open_risk'] * equity:.2f}"]}
    room = L["max_gross_exposure"] * equity - gross
    if room <= 0:
        return {"approved": False, "reasons": [f"gross_exposure_cap: {gross / equity:.2f}x >= {L['max_gross_exposure']}x"]}

    entry_fill = entry * (1 + side * slip)
    stop_fill = stop * (1 - side * slip * 1.5)                       # stops slip more than entries
    funding = 0.0
    if num(req, "funding_rate_per_interval") is not None and num(req, "expected_hold_h"):
        n_fund = num(req, "expected_hold_h") / (num(req, "funding_interval_h") or 8.0)
        funding = max(0.0, side * num(req, "funding_rate_per_interval")) * n_fund * entry   # only count it when it is a cost
    loss_per_unit = ((entry_fill - stop_fill) * side + (entry_fill + stop_fill) * taker + funding) * fx
    qty = min(budget / loss_per_unit, room / (entry * fx), num(c, "max_qty") or math.inf)
    qty = round_down(qty, num(c, "qty_step"))
    notional = qty * entry * fx
    if qty < num(c, "min_qty") or notional < num(c, "min_notional"):
        return {"approved": False, "reasons": [f"below_minimum_order: qty={qty} notional={notional:.2f}"]}

    stop_pct = abs(entry - stop) / entry
    lev = int(max(1, min(num(c, "max_leverage") or 1, MAX_LEVERAGE_SETTING,
                         math.floor(1.0 / (LIQ_BUFFER_MULT * stop_pct + ASSUMED_MMR)))))
    notes = []
    requested = num(req, "requested_leverage")
    if requested is not None:
        if requested > lev:
            notes.append(f"requested leverage {requested:g}x ignored: it would put liquidation inside "
                         f"{LIQ_BUFFER_MULT:g} stop-distances; using {lev}x. Position size is unchanged either way.")
        elif requested >= 1:
            lev = int(requested)
    margin = notional / lev
    if margin > available * MAX_MARGIN_SHARE:
        qty = round_down(available * MAX_MARGIN_SHARE * lev / (entry * fx), num(c, "qty_step"))
        notional, margin = qty * entry * fx, qty * entry * fx / lev
        notes.append("size reduced to fit available margin (leverage was not raised)")
        if qty < num(c, "min_qty") or notional < num(c, "min_notional"):
            return {"approved": False, "reasons": ["insufficient_available_margin"]}
    liq = entry * (1 - side * (1.0 / lev - ASSUMED_MMR))
    if (stop - liq) * side <= 0:
        return {"approved": False, "reasons": ["liquidation_before_stop"]}

    r_unit = abs(entry - stop)
    tick = num(c, "price_tick")
    trail = str(req.get("exit_mode", "targets")).lower() == "trail"
    tp1_r, tp2_r = num(req, "tp1_r") or 1.0, num(req, "tp2_r") or 3.0
    frac = num(req, "tp1_fraction") if num(req, "tp1_fraction") is not None else 0.5
    tp1, tp2 = round_price(entry + side * tp1_r * r_unit, tick), round_price(entry + side * tp2_r * r_unit, tick)
    tp1_qty = round_down(qty * frac, num(c, "qty_step"))
    tp2_qty = round_down(qty - tp1_qty, num(c, "qty_step"))
    if trail:
        tp1_qty, tp2_qty = 0.0, qty                 # no fixed targets: the whole position rides the trailing stop
    elif tp1_qty < num(c, "min_qty") or tp2_qty < num(c, "min_qty"):
        tp1_qty, tp2_qty = 0.0, qty
        notes.append("position too small to split: single target at TP2")
    if not trail and (tp1 <= 0 or tp2 <= 0):
        return {"approved": False, "reasons": ["target_not_a_valid_price: stop distance too wide for this instrument"]}
    planned_loss = qty * loss_per_unit
    fees_win = (qty * entry_fill * taker + tp1_qty * tp1 * maker + tp2_qty * tp2 * maker) * fx
    reward = (tp1_qty * (tp1 - entry_fill) + tp2_qty * (tp2 - entry_fill)) * side * fx - fees_win - qty * funding * fx
    net_rr = reward / planned_loss if planned_loss > 0 else 0.0
    verified = str(req.get("equity_source", "verified")).lower() == "verified"
    if not verified:
        notes.append("INDICATIVE ONLY: equity was stated by the user, not read from the account. Re-run with verified account state before any order.")
    plan = {
        "symbol": c.get("symbol"), "side": "long" if side == 1 else "short",
        "entry": round_price(entry, tick), "stop_loss": round_price(stop, tick),
        "exit_mode": "trail" if trail else "targets",
        "take_profit_1": None if trail else tp1, "take_profit_2": None if trail else tp2,
        "trail_rule": (req.get("trail_rule") or "trailing stop per strategy; never moved against the position") if trail else None,
        "reference_3r_price": round_price(entry + side * 3 * r_unit, tick) if entry + side * 3 * r_unit > 0 else None,
        "position_size": qty, "tp1_size": tp1_qty, "tp2_size": tp2_qty,
        "notional": round(notional, 2), "leverage_setting": lev, "margin_mode": "isolated",
        "effective_leverage": round(notional / equity, 4),
        "portfolio_leverage_after": round((gross + notional) / equity, 4),
        "margin_required": round(margin, 2), "estimated_liquidation_price": round_price(liq, tick),
        "maximum_planned_loss": round(planned_loss, 2), "risk_pct_of_equity": round(planned_loss / equity, 6),
        "estimated_execution_cost": round(qty * ((entry_fill + stop_fill) * taker + (abs(entry_fill - entry) + abs(stop_fill - stop))) * fx, 2),
        "estimated_funding_cost": round(qty * funding * fx, 2),
        "net_reward_risk": None if trail else round(net_rr, 3),
        "equity_source": "verified" if verified else "user_stated", "indicative_only": not verified,
        "notes": notes,
    }
    eps = 1e-9
    bad = []
    if planned_loss / equity > L["risk_per_trade"] + eps: bad.append("invariant_risk_per_trade")
    if open_risk + planned_loss > L["max_open_risk"] * equity + eps: bad.append("invariant_open_risk")
    if (gross + notional) / equity > L["max_gross_exposure"] + eps: bad.append("invariant_gross_exposure")
    if not trail and net_rr < 1.0: bad.append(f"net_reward_risk_below_1: {net_rr:.2f}")
    return {"approved": not bad, "reasons": bad, "plan": plan if not bad else None, "limits": L}


if __name__ == "__main__":
    res = size(load_input(sys.argv))
    sys.exit(emit(res, res["approved"]))
