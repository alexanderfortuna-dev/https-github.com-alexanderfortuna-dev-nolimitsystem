"""Bitget Unified Trading Account (v3) REST client.

Security properties:
  * Secrets are held only in memory and used only for HMAC signing.
  * Request/response logging is redacted (no key, signature, passphrase).
  * A client built with production credentials can only issue GET requests.
    Any POST raises ProductionTradingDisabled before touching the network.
  * A demo client always sends the `paptrading: 1` header; POST is allowed
    only on an explicit allow-list of trade paths.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests

from ..config import ApiCredentials
from .. import LIVE_TRADING_ENABLED

log = logging.getLogger("aplus.bitget")

BASE_URL = "https://api.bitget.com"

# The only state-changing endpoints the system may ever call, and only in demo.
DEMO_WRITE_ALLOWLIST = frozenset({
    "/api/v3/trade/place-order",
    "/api/v3/trade/cancel-order",
    "/api/v3/trade/place-strategy-order",
    "/api/v3/trade/cancel-strategy-order",
    "/api/v3/trade/modify-strategy-order",
    "/api/v3/account/set-leverage",
})


class BitgetAPIError(RuntimeError):
    def __init__(self, code: str, msg: str, path: str, http_status: int | None = None):
        super().__init__(f"Bitget API error {code} on {path}: {msg}")
        self.code, self.msg, self.path, self.http_status = code, msg, path, http_status


class ProductionTradingDisabled(RuntimeError):
    """Raised on any attempt to send a state-changing request outside demo."""


class StaleDataError(RuntimeError):
    pass


class BitgetClient:
    def __init__(self, creds: ApiCredentials | None = None, *, demo: bool = False,
                 base_url: str = BASE_URL, session: requests.Session | None = None,
                 timeout: float = 15.0, max_retries: int = 3):
        self._creds = creds
        self.demo = demo
        self.base_url = base_url.rstrip("/")
        self._s = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.clock_offset_ms = 0
        self.last_ok_ts: float | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ signing
    @staticmethod
    def sign(secret: str, timestamp: str, method: str, path: str, query: str = "", body: str = "") -> str:
        prehash = timestamp + method.upper() + path + (("?" + query) if query else "") + body
        digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, method: str, path: str, query: str, body: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if self.demo:
            headers["paptrading"] = "1"
        if self._creds and self._creds.present:
            ts = str(int(time.time() * 1000) + self.clock_offset_ms)
            headers.update({
                "ACCESS-KEY": self._creds.key,
                "ACCESS-SIGN": self.sign(self._creds.secret, ts, method, path, query, body),
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self._creds.passphrase,
            })
        return headers

    # ------------------------------------------------------------------ guards
    def _guard_write(self, method: str, path: str) -> None:
        if method.upper() == "GET":
            return
        if LIVE_TRADING_ENABLED:  # pragma: no cover - constant is False
            raise ProductionTradingDisabled("LIVE_TRADING_ENABLED must stay False")
        if not self.demo:
            raise ProductionTradingDisabled(
                f"Blocked {method} {path}: state-changing requests are only allowed on the Demo Trading client."
            )
        if path not in DEMO_WRITE_ALLOWLIST:
            raise ProductionTradingDisabled(f"Blocked {method} {path}: path not on the demo write allow-list.")

    # ------------------------------------------------------------------ transport
    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                *, signed: bool = False) -> Any:
        method = method.upper()
        self._guard_write(method, path)
        if signed and not (self._creds and self._creds.present):
            raise BitgetAPIError("NO_CREDENTIALS", "API credentials are not configured", path)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(sorted(params.items())) if params else ""
        body_s = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = self.base_url + path + (("?" + query) if query else "")
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            headers = self._headers(method, path, query, body_s) if signed else {
                "Content-Type": "application/json", **({"paptrading": "1"} if self.demo else {})}
            try:
                resp = self._s.request(method, url, data=body_s or None, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                self.last_error = f"{type(exc).__name__} on {path}"
                # Never auto-retry a write after a transport error: the order may have been accepted.
                # The caller must reconcile by clientOid instead.
                if method != "GET":
                    raise
                time.sleep(0.5 * (2 ** attempt))
                continue
            if resp.status_code == 429 and method == "GET":
                time.sleep(0.5 * (2 ** attempt))
                continue
            try:
                payload = resp.json()
            except ValueError:
                self.last_error = f"HTTP {resp.status_code} non-JSON on {path}"
                raise BitgetAPIError("BAD_RESPONSE", f"HTTP {resp.status_code} non-JSON body", path, resp.status_code)
            code = str(payload.get("code"))
            if code != "00000":
                self.last_error = f"{code} on {path}"
                log.warning("bitget error path=%s code=%s msg=%s", path, code, payload.get("msg"))
                raise BitgetAPIError(code, str(payload.get("msg")), path, resp.status_code)
            self.last_ok_ts = time.time()
            self.last_error = None
            return payload.get("data")
        raise BitgetAPIError("NETWORK", f"request failed after retries: {type(last_exc).__name__ if last_exc else '429'}", path)

    def get(self, path: str, params: dict | None = None, *, signed: bool = False) -> Any:
        return self.request("GET", path, params=params, signed=signed)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, body=body, signed=True)

    # ------------------------------------------------------------------ public market data
    def server_time_ms(self) -> int:
        return int(self.get("/api/v2/public/time")["serverTime"])

    def sync_clock(self) -> int:
        t0 = time.time()
        server = self.server_time_ms()
        t1 = time.time()
        self.clock_offset_ms = int(server - (t0 + t1) / 2 * 1000)
        return self.clock_offset_ms

    def instruments(self, category: str = "USDT-FUTURES", symbol: str | None = None) -> list[dict]:
        return self.get("/api/v3/market/instruments", {"category": category, "symbol": symbol}) or []

    def tickers(self, category: str = "USDT-FUTURES", symbol: str | None = None) -> list[dict]:
        return self.get("/api/v3/market/tickers", {"category": category, "symbol": symbol}) or []

    def candles(self, symbol: str, interval: str, *, category: str = "USDT-FUTURES", limit: int = 100,
                end_time: int | None = None, history: bool = False) -> list[list[str]]:
        path = "/api/v3/market/history-candles" if history else "/api/v3/market/candles"
        return self.get(path, {"category": category, "symbol": symbol, "interval": interval,
                               "limit": limit, "endTime": end_time}) or []

    def current_funding(self, symbol: str) -> dict:
        data = self.get("/api/v3/market/current-fund-rate", {"symbol": symbol}) or []
        return data[0] if data else {}

    def funding_history(self, symbol: str, *, category: str = "USDT-FUTURES", limit: int = 100,
                        cursor: int | None = None) -> list[dict]:
        data = self.get("/api/v3/market/history-fund-rate",
                        {"category": category, "symbol": symbol, "limit": limit, "cursor": cursor}) or {}
        return data.get("resultList", []) if isinstance(data, dict) else data

    # ------------------------------------------------------------------ private read-only
    def account_info(self) -> dict:
        return self.get("/api/v3/account/info", signed=True) or {}

    def account_settings(self) -> dict:
        return self.get("/api/v3/account/settings", signed=True) or {}

    def account_assets(self) -> dict:
        return self.get("/api/v3/account/assets", signed=True) or {}

    def funding_assets(self) -> list[dict]:
        return self.get("/api/v3/account/funding-assets", signed=True) or []

    def positions(self, category: str = "USDT-FUTURES", symbol: str | None = None) -> list[dict]:
        data = self.get("/api/v3/position/current-position", {"category": category, "symbol": symbol}, signed=True)
        return (data or {}).get("list", []) if isinstance(data, dict) else (data or [])

    def open_orders(self, category: str | None = None, symbol: str | None = None) -> list[dict]:
        data = self.get("/api/v3/trade/unfilled-orders", {"category": category, "symbol": symbol}, signed=True)
        return (data or {}).get("list", []) if isinstance(data, dict) else (data or [])

    def open_strategy_orders(self, category: str = "USDT-FUTURES", type_: str | None = None) -> list[dict]:
        data = self.get("/api/v3/trade/unfilled-strategy-orders", {"category": category, "type": type_}, signed=True)
        return (data or {}).get("list", []) if isinstance(data, dict) else (data or [])

    # other ledgers (read-only) used by the audit to avoid double counting
    def all_account_balance(self) -> list[dict]:
        """Classic overview: per-account-type USDT totals. Used only as a cross-check."""
        return self.get("/api/v2/account/all-account-balance", signed=True) or []

    def bot_assets(self, account_type: str) -> list[dict]:
        """Strategy/bot account assets (classic v2). account_type: futures | spot."""
        return self.get("/api/v2/account/bot-assets", {"accountType": account_type}, signed=True) or []

    def cfd_fund_detail(self) -> dict:
        return self.get("/api/v3/cfd/account/fund-detail", signed=True) or {}

    def cfd_positions(self) -> list[dict]:
        return self.get("/api/v3/cfd/trade/current-positions", signed=True) or []

    def grid_bot_detail(self, bot_id: str) -> dict:
        return self.get("/api/v3/trade/grid/bot-detail", {"botId": bot_id}, signed=True) or {}

    def loan_data(self) -> dict:
        return self.get("/api/v3/trade/loan-data", signed=True) or {}

    def order_info(self, *, order_id: str | None = None, client_oid: str | None = None) -> dict:
        return self.get("/api/v3/trade/order-info", {"orderId": order_id, "clientOid": client_oid}, signed=True) or {}

    def fills(self, category: str = "USDT-FUTURES", order_id: str | None = None, limit: int = 100) -> list[dict]:
        data = self.get("/api/v3/trade/fills", {"category": category, "orderId": order_id, "limit": limit}, signed=True)
        return (data or {}).get("list", []) if isinstance(data, dict) else (data or [])

    def fee_rate(self, category: str, symbol: str) -> dict:
        return self.get("/api/v3/account/fee-rate", {"category": category, "symbol": symbol}, signed=True) or {}

    def financial_records(self, category: str = "USDT-FUTURES", type_: str | None = None, limit: int = 100,
                          start_time: int | None = None, end_time: int | None = None) -> list[dict]:
        data = self.get("/api/v3/account/financial-records",
                        {"category": category, "type": type_, "limit": limit,
                         "startTime": start_time, "endTime": end_time}, signed=True)
        return (data or {}).get("list", []) if isinstance(data, dict) else (data or [])

    # ------------------------------------------------------------------ demo-only writes
    def place_order(self, body: dict) -> dict:
        return self.post("/api/v3/trade/place-order", body) or {}

    def cancel_order(self, body: dict) -> dict:
        return self.post("/api/v3/trade/cancel-order", body) or {}

    def place_strategy_order(self, body: dict) -> dict:
        return self.post("/api/v3/trade/place-strategy-order", body) or {}

    def cancel_strategy_order(self, body: dict) -> dict:
        return self.post("/api/v3/trade/cancel-strategy-order", body) or {}

    def modify_strategy_order(self, body: dict) -> dict:
        return self.post("/api/v3/trade/modify-strategy-order", body) or {}

    def set_leverage(self, body: dict) -> Any:
        return self.post("/api/v3/account/set-leverage", body)
