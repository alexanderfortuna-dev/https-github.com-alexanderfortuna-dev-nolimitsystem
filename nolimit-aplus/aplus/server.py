"""Local control API for n8n. Binds to 127.0.0.1 by default and requires a bearer token.
There is no endpoint that enables live trading or accepts API keys."""
from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dashboard import collect, render

log = logging.getLogger("aplus.server")


def make_handler(app):
    lock = threading.Lock()                      # one engine operation at a time
    token = app.settings.api_token

    class H(BaseHTTPRequestHandler):
        server_version = "aplus"

        def log_message(self, fmt, *args):       # no query strings / headers in logs
            log.info("%s %s", self.command, self.path.split("?")[0])

        def _send(self, code: int, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authed(self) -> bool:
            got = self.headers.get("Authorization", "")
            return bool(token) and hmac.compare_digest(got, f"Bearer {token}")

        def _body(self) -> dict:
            n = min(int(self.headers.get("Content-Length") or 0), 65536)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":                # unauthenticated liveness only, no data
                return self._send(200, {"ok": True})
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            with lock:
                if path == "/status":
                    d = collect(app)
                    return self._send(200, {"mode": d["mode"], "kill": d["kill"], "connectivity": d["connectivity"],
                                            "exec": d["exec"], "approved": len(d["approved"]),
                                            "heartbeats": d["heartbeats"], "live_trading_enabled": False})
                if path == "/dashboard":
                    return self._send(200, render(collect(app)), "text/html; charset=utf-8")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            body = self._body()
            try:
                with lock:
                    if path == "/run/scan":
                        c = app.scan(execute=bool(body.get("execute", True)))
                        return self._send(200, {"signals": len(c), "approved": [x["candidate_key"] for x in c if x["approved"]]})
                    if path == "/run/tick":
                        app.tick()
                        return self._send(200, {"ok": True})
                    if path == "/run/reconcile":
                        return self._send(200, {"findings": app.engine.reconcile()})
                    if path == "/run/audit":
                        r = app.audit(body.get("bots_usdt"))
                        return self._send(200, {"complete": r.complete, "total_equity": r.total_equity,
                                                "ledgers": r.ledgers, "issues": r.issues})
                    if path == "/kill":
                        app.engine.emergency_stop(str(body.get("reason") or "manual via API"), bool(body.get("flatten")))
                        return self._send(200, {"kill_switch": True})
                    if path == "/tv/alert":
                        # TradingView alerts are context only: they are logged and can never create a trade.
                        app.store.event("info", "tradingview_alert", str(body.get("message", ""))[:300],
                                        {k: body.get(k) for k in ("symbol", "interval", "strategy", "side", "price")})
                        return self._send(200, {"logged": True, "authorises_trade": False})
            except SystemExit as exc:
                return self._send(409, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                log.exception("request failed")
                return self._send(500, {"error": type(exc).__name__})
            self._send(404, {"error": "not found"})

    return H


def serve(app) -> None:
    if not app.settings.api_token or len(app.settings.api_token) < 24:
        raise SystemExit("Set APLUS_API_TOKEN (>= 24 random characters) before starting the control API.")
    srv = ThreadingHTTPServer((app.settings.api_host, app.settings.api_port), make_handler(app))
    log.info("control API on http://%s:%s", app.settings.api_host, app.settings.api_port)
    srv.serve_forever()
