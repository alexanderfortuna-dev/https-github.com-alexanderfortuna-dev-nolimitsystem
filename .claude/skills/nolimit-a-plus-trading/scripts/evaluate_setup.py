#!/usr/bin/env python3
"""A+++ setup engine: transparent 100-point score plus seven hard gates.

A score is a research ranking, NOT a win probability. 95/100 does not mean 95% chance of profit.
A setup becomes TRADE CANDIDATE only when the score is >= 90 AND every gate passes.
Missing information fails closed (INSUFFICIENT DATA), it is never assumed favourable.

Usage: python evaluate_setup.py setup.json        (see examples/valid_trade.json, examples/rejected_trade.json)
"""
from __future__ import annotations

import json
import os
import sys

from _common import emit, load_input

RUBRIC = {                     # component -> maximum points (sums to 100)
    "market_structure": 15,    # regime and trend agree across the timeframes analysed
    "entry_quality": 15,       # objective trigger, not late, clear invalidation
    "statistical_edge": 20,    # out-of-sample net expectancy for this strategy on this asset group
    "liquidity": 10,           # turnover, depth, spread
    "volatility": 5,           # stop sits outside noise; no volatility shock
    "derivatives_evidence": 10,# OI, funding, positioning corroborate (never used alone)
    "macro_event_risk": 10,    # no high-impact scheduled event inside the holding window
    "execution_cost": 5,       # fees + spread + slippage small relative to the stop distance
    "risk_reward": 10,         # net reward/risk after costs
}
GATES = ("data_quality_verified", "objective_entry_and_invalidation", "out_of_sample_evidence",
         "positive_net_expectancy", "liquidity_sufficient", "portfolio_risk_acceptable", "adversarial_review_passed")
SCORE_THRESHOLD = 90
REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "strategy_registry.json")


def registry_status(strategy_id: str | None) -> tuple[str, dict]:
    try:
        reg = json.load(open(REGISTRY))
    except Exception:  # noqa: BLE001
        return "UNKNOWN", {}
    for s in reg.get("strategies", []):
        if s.get("id") == strategy_id:
            return s.get("status", "UNKNOWN"), s
    return "UNKNOWN", {}


def evaluate(setup: dict) -> dict:
    scores = setup.get("scores") or {}
    gates = setup.get("gates") or {}
    problems, breakdown, total = [], {}, 0.0
    for comp, cap in RUBRIC.items():
        v = scores.get(comp)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            problems.append(f"score missing: {comp}")
            breakdown[comp] = {"points": None, "max": cap}
            continue
        if v < 0 or v > cap:
            problems.append(f"score out of range: {comp}={v} (0..{cap})")
            v = max(0, min(cap, v))
        breakdown[comp] = {"points": v, "max": cap}
        total += v

    reg_status, reg_entry = registry_status(setup.get("strategy"))
    gate_results = {}
    for g in GATES:
        v = gates.get(g)
        gate_results[g] = bool(v) if isinstance(v, bool) else None
    # The registry is authoritative for evidence: a caller cannot assert an edge the research does not support.
    if reg_status != "VALIDATED":
        if gate_results["out_of_sample_evidence"] or gate_results["positive_net_expectancy"]:
            problems.append(f"evidence gates overridden: strategy {setup.get('strategy')!r} is {reg_status} in the registry")
        gate_results["out_of_sample_evidence"] = False
        gate_results["positive_net_expectancy"] = False
        cap = 0 if reg_status in ("REJECTED", "UNKNOWN") else 8
        if (breakdown["statistical_edge"]["points"] or 0) > cap:
            total -= breakdown["statistical_edge"]["points"] - cap
            breakdown["statistical_edge"]["points"] = cap
            breakdown["statistical_edge"]["capped_by_registry"] = reg_status

    md = setup.get("market_data_status")
    if md != "VERIFIED":
        gate_results["data_quality_verified"] = False if md else None
    if not (setup.get("entry") and setup.get("stop") and setup.get("invalidation")):
        gate_results["objective_entry_and_invalidation"] = False
    conflicts = setup.get("conflicting_signals") or []

    missing_gates = [g for g, v in gate_results.items() if v is None]
    failed_gates = [g for g, v in gate_results.items() if v is False]
    missing_scores = [c for c, b in breakdown.items() if b["points"] is None]
    score = round(total, 1)

    if missing_scores or md is None or "data_quality_verified" in missing_gates:
        decision = "INSUFFICIENT DATA"
    elif "data_quality_verified" in failed_gates:
        decision = "INSUFFICIENT DATA"
    elif reg_status in ("REJECTED", "UNKNOWN"):
        decision = "NO TRADE"
    elif failed_gates or missing_gates:
        # an unvalidated-but-promising strategy, or a setup that is not ready yet
        decision = "WAIT" if set(failed_gates + missing_gates) <= {"out_of_sample_evidence", "positive_net_expectancy",
                                                                    "adversarial_review_passed"} else "NO TRADE"
    elif score < SCORE_THRESHOLD or conflicts:
        decision = "WAIT"
    else:
        decision = "TRADE CANDIDATE"

    return {
        "decision": decision, "setup_score": score, "score_threshold": SCORE_THRESHOLD,
        "score_is_not_a_probability": True,
        "preliminary_research_qualification": score >= SCORE_THRESHOLD and not missing_scores,
        "validation_status": reg_status, "strategy_evidence": reg_entry.get("evidence"),
        "breakdown": breakdown, "gates": gate_results, "failed_gates": failed_gates, "missing_gates": missing_gates,
        "conflicting_signals": conflicts, "problems": problems,
        "executable": decision == "TRADE CANDIDATE",
        "execution_mode": "paper only; live order submission is not available in this skill",
    }


if __name__ == "__main__":
    res = evaluate(load_input(sys.argv))
    sys.exit(emit(res, res["decision"] == "TRADE CANDIDATE"))
