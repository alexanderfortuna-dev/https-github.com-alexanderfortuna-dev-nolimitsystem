"""Owner dashboard: collects state from the store and renders one self-contained HTML page."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import LIVE_TRADING_ENABLED
from ..universe import UNSUPPORTED

HERE = Path(__file__).parent


def _ts(ms) -> str:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return "-"


def collect(app) -> dict:
    s, mode = app.store, app.engine.mode
    snaps = s.select("account_snapshots", {"mode": mode}, order="ts.asc")
    prod = s.select("account_snapshots", {"mode": "production_readonly"}, order="ts.desc", limit=1)
    eq = [float(x["equity"]) for x in snaps if x["complete"]]
    peak = max(eq) if eq else 0.0
    cur = eq[-1] if eq else 0.0
    acct = app.engine.account_state(snapshot=False)
    limits = app.settings.risk
    trades = s.select("trades", {"mode": mode}, order="created_at.desc", limit=200)
    px = {p.symbol: p.mark_price for p in acct.positions}
    for t in trades:
        t["unrealized"] = ((px[t["symbol"]] - t["entry_fill"]) * t["side"] * t["qty_open"]
                           if t["state"] in ("OPEN", "TP1_DONE") and t["symbol"] in px and t["entry_fill"] else 0.0)
        t["fills"] = s.select("fills", {"trade_key": t["trade_key"]})
    closed = [t for t in trades if t["state"] == "CLOSED"]
    cands = s.select("candidates", order="ts.desc", limit=300)
    recent_cut = time.time() * 1000 - 3 * 86_400_000
    cands = [c for c in cands if c["signal_ts"] >= recent_cut]
    summary_file = Path(app.settings.var_dir).parent / "reports" / "validation_summary.json"
    validation = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    day = (acct.equity / acct.day_start_equity - 1) if acct.day_start_equity else 0.0
    week = (acct.equity / acct.week_start_equity - 1) if acct.week_start_equity else 0.0
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "mode": mode, "live_trading_enabled": LIVE_TRADING_ENABLED,
        "kill": {"active": app.kill.active, "reason": app.kill.reason()},
        "connectivity": app.connectivity(),
        "heartbeats": s.select("heartbeats"),
        "production": prod[0] if prod else None,
        "exec": {"equity": acct.equity, "available": acct.available, "peak": peak,
                 "drawdown": (peak - cur) / peak if peak > 0 else 0.0,
                 "open_risk": acct.open_planned_risk, "open_risk_pct": acct.open_planned_risk / acct.equity if acct.equity else 0,
                 "gross": acct.gross_exposure, "gross_lev": acct.gross_leverage if acct.equity else 0,
                 "day": day, "week": week, "breakers": app.risk.circuit_breakers(acct),
                 "realized": sum(float(t["realized_pnl"] or 0) - float(t["fees"] or 0) for t in closed),
                 "unrealized": sum(t["unrealized"] for t in trades), "closed_n": len(closed),
                 "mean_r": (sum(float(t["net_r"] or 0) for t in closed) / len(closed)) if closed else None},
        "limits": limits,
        "approved": [c for c in cands if c["approved"]],
        "rejected": [c for c in cands if not c["approved"]][:40],
        "trades": trades[:50],
        "bots": s.select("bot_allocations"),
        "validation": validation,
        "events": s.select("events", order="id.desc", limit=40),
        "unsupported": UNSUPPORTED,
    }


def render(data: dict) -> str:
    env = Environment(loader=FileSystemLoader(HERE), autoescape=select_autoescape(["html"]))
    env.filters["ts"] = _ts
    env.filters["usd"] = lambda v: "-" if v is None else f"{float(v):,.2f}"
    env.filters["pct"] = lambda v: "-" if v is None else f"{float(v) * 100:+.2f}%"
    env.filters["num"] = lambda v, d=4: "-" if v is None else f"{float(v):,.{d}f}".rstrip("0").rstrip(".")
    env.filters["r"] = lambda v: "-" if v is None or v == "" else f"{float(v):+.2f}R"
    return env.get_template("template.html").render(**data)
