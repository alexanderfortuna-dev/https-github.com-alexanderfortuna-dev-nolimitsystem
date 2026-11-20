"""Shared helpers for the nolimit-a-plus-trading scripts. Standard library only."""
from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN

# Provisional operating limits (paper configuration). Callers may TIGHTEN them, never loosen.
LIMITS = {
    "risk_per_trade": 0.005,
    "max_open_risk": 0.015,
    "daily_loss_limit": 0.015,
    "weekly_loss_limit": 0.04,
    "max_gross_exposure": 3.0,
}
LIVE_EXECUTION_ENABLED = False          # constant; there is no flag that turns it on


def tightened(overrides: dict | None) -> dict:
    out = dict(LIMITS)
    for k, v in (overrides or {}).items():
        if k in out and isinstance(v, (int, float)) and v > 0:
            out[k] = min(float(v), out[k])
    return out


def load_input(argv: list[str]) -> dict:
    """Read JSON from a file path argument or from stdin."""
    args = [a for a in argv[1:] if not a.startswith("--")]
    raw = open(args[0]).read() if args else sys.stdin.read()
    return json.loads(raw)


def emit(obj: dict, ok: bool) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0 if ok else 2


def num(d: dict, key: str, required: bool = True):
    """Return a finite positive float, or None. Missing required numbers are reported, never guessed."""
    v = d.get(key)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def round_down(value: float, step: float) -> float:
    s = Decimal(str(step))
    return float((Decimal(str(value)) / s).to_integral_value(rounding=ROUND_DOWN) * s)


def round_price(value: float, tick: float) -> float:
    t = Decimal(str(tick))
    return float((Decimal(str(value)) / t).to_integral_value(rounding=ROUND_HALF_EVEN) * t)
