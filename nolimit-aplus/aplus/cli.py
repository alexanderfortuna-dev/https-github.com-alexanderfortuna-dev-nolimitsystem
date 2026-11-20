"""Command line entry point:  python -m aplus <command>"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import ROOT, Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aplus", description="NO LIMIT A+++ (paper/demo only; live trading is disabled)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="connectivity, mode, kill switch")
    sub.add_parser("sync-data", help="download/refresh history for the scan universe")
    sub.add_parser("validate", help="run backtests + validation, write reports/")
    p = sub.add_parser("scan", help="scan the market once")
    p.add_argument("--execute", action="store_true", help="arm approved plans on the paper/demo broker")
    sub.add_parser("tick", help="advance the execution engine once")
    p = sub.add_parser("run", help="loop: scan on bar close, tick every few seconds")
    p.add_argument("--tick-seconds", type=float, default=5.0)
    p.add_argument("--scan-minutes", type=float, default=15.0)
    p = sub.add_parser("audit", help="read-only audit of the production account")
    p.add_argument("--bots-usdt", type=float, help="manual bot capital if the bot account cannot be read")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("dashboard", help="write the owner dashboard HTML")
    p.add_argument("--out", default=str(ROOT / "var" / "dashboard.html"))
    sub.add_parser("serve", help="start the local control API for n8n")
    p = sub.add_parser("kill", help="engage the emergency kill switch")
    p.add_argument("reason", nargs="?", default="manual")
    p.add_argument("--flatten", action="store_true", help="also close positions opened by this system")
    sub.add_parser("unkill", help="release the kill switch")
    sub.add_parser("reconcile", help="compare broker state with the book")
    sub.add_parser("demo-smoke", help="read-only checks against the Bitget Demo Trading API")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if a.cmd in ("sync-data", "validate"):
        from .bitget.client import BitgetClient
        from . import research
        from .universe import build_universe
        client, data = BitgetClient(), ROOT / "data"
        if a.cmd == "sync-data":
            u = build_universe(client)
            research.save_universe(u, data / "universe.json")
            rep = research.sync_data(client, u, data)
            (data / "sync_report.json").write_text(json.dumps(rep, indent=1))
            print(f"synced {len(u)} instruments")
        else:
            r = research.run_validation(client, data, ROOT / "reports")
            s = r["summary"]
            print(f"run {s['run_id']}: {s['total_trades']} trades, pools passed {s['pools_passed']}/{len(s['pools'])}, "
                  f"symbols passed {s['symbols_passed']}")
        return 0

    from .app import App
    app = App.build(Settings.from_env())
    if a.cmd == "status":
        print(json.dumps({"mode": app.engine.mode, "live_trading_enabled": False,
                          "kill_switch": {"active": app.kill.active, "reason": app.kill.reason()},
                          "connectivity": app.connectivity(), "risk_limits": app.settings.risk.__dict__}, indent=1))
    elif a.cmd == "scan":
        c = app.scan(execute=a.execute)
        ok = [x for x in c if x["approved"]]
        print(f"{len(c)} technical signals, {len(ok)} approved")
        for x in c:
            lv = x["evidence"]["levels"]
            print(f"  {x['grade']:<16} {x['symbol']:<12} {x['strategy']} {x['interval']} {'LONG' if x['side'] == 1 else 'SHORT':<5} "
                  f"entry {lv['entry']} stop {lv['stop']} tp1 {lv['tp1']} tp2 {lv['tp2']}  {'; '.join(x['reasons'])[:90]}")
    elif a.cmd == "tick":
        app.tick()
    elif a.cmd == "run":
        last_scan = 0.0
        while True:
            try:
                if time.time() - last_scan > a.scan_minutes * 60:
                    app.scan(execute=True)
                    app.engine.reconcile()
                    last_scan = time.time()
                app.tick()
            except KeyboardInterrupt:
                return 0
            except Exception:  # noqa: BLE001
                logging.exception("loop error (no new entries are sent on error)")
            time.sleep(a.tick_seconds)
    elif a.cmd == "audit":
        r = app.audit(a.bots_usdt)
        if a.json:
            print(json.dumps(r.to_dict(), indent=1, default=str))
        else:
            print(f"complete: {r.complete}\ntotal equity (USD): {r.total_equity:,.2f}")
            for k, v in r.ledgers.items():
                print(f"  ledger {k:<8} {'unavailable' if v is None else format(v, ',.2f')}")
            print(f"available USDT: {r.available_usdt:,.2f} | liabilities: {r.liabilities_usd:,.2f} | "
                  f"gross exposure: {r.gross_exposure:,.2f} ({r.effective_leverage:.2f}x) | uPnL: {r.unrealized_pnl:,.2f}")
            print(f"positions: {len(r.positions)} | pending orders: {len(r.pending_orders)} | "
                  f"strategy orders: {len(r.pending_strategy_orders)}")
            for i in r.issues:
                print("  !", i)
        return 0 if r.complete else 2
    elif a.cmd == "dashboard":
        from .dashboard import collect, render
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(collect(app)))
        print(out)
    elif a.cmd == "serve":
        from .server import serve
        serve(app)
    elif a.cmd == "kill":
        app.engine.emergency_stop(a.reason, a.flatten)
        print("kill switch ENGAGED")
    elif a.cmd == "unkill":
        app.kill.release()
        print("kill switch released")
    elif a.cmd == "reconcile":
        for f in app.engine.reconcile() or ["no findings"]:
            print(f)
    elif a.cmd == "demo-smoke":
        from .bitget.client import BitgetClient
        if not app.settings.demo_creds.present:
            print("BITGET_DEMO_API_* not set")
            return 2
        c = BitgetClient(app.settings.demo_creds, demo=True)
        c.sync_clock()
        for name, fn in (("account_info", c.account_info), ("account_settings", c.account_settings),
                         ("account_assets", c.account_assets), ("positions", lambda: c.positions("USDT-FUTURES")),
                         ("open_orders", c.open_orders),
                         ("strategy_orders", lambda: c.open_strategy_orders("USDT-FUTURES", "tpsl"))):
            try:
                res = fn()
                brief = {k: res.get(k) for k in ("permType", "holdMode", "accountEquity") if k in res} if isinstance(res, dict) else f"{len(res)} rows"
                print(f"PASS {name}: {brief}")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {name}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
