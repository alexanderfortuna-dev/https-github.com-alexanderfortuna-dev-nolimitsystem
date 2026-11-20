"""Bitget Demo Trading broker (UTA v3, header `paptrading: 1`, separate demo API key).

Protection is atomic: the entry is a market order that carries a preset stop-loss
(`stopLoss`, triggered on mark price), so a position never exists without a stop.
Exits are reduce-only. Margin mode is isolated. The account must be in one-way mode;
this broker never changes hold mode by itself.

STATUS: request shapes follow the official documentation and are unit-tested against a
recorded-shape mock. They have NOT yet been exercised against the real demo environment
(no demo key was available at build time). Run `aplus demo-smoke` after adding demo keys.
"""
from __future__ import annotations

import time

from ..bitget.client import BitgetAPIError, BitgetClient
from ..bitget.models import Instrument
from .broker import BrokerError, Fill, OrderResult

CATEGORY = "USDT-FUTURES"
STATUS_MAP = {"live": "live", "new": "live", "partially_filled": "partially_filled", "filled": "filled",
              "cancelled": "cancelled", "canceled": "cancelled"}


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class DemoBroker:
    mode = "demo"

    def __init__(self, client: BitgetClient):
        if not client.demo:
            raise BrokerError("DemoBroker requires a demo client (paptrading header). Production trading is disabled.")
        self.c = client
        self._hold_mode_checked = False

    def _check_hold_mode(self) -> None:
        if self._hold_mode_checked:
            return
        mode = self.c.account_settings().get("holdMode")
        if mode != "one_way_mode":
            raise BrokerError(f"demo account holdMode is {mode!r}; set it to one_way_mode in the Bitget demo UI")
        self._hold_mode_checked = True

    def account(self) -> dict:
        issues = []
        try:
            a = self.c.account_assets()
        except Exception as exc:  # noqa: BLE001
            return {"equity": 0.0, "available": 0.0, "complete": False, "issues": [f"assets: {exc}"]}
        usdt = next((x for x in a.get("assets") or [] if x.get("coin") == "USDT"), {})
        return {"equity": _f(a.get("accountEquity")), "available": _f(usdt.get("available")),
                "complete": True, "issues": issues}

    def positions(self) -> list[dict]:
        out = []
        for p in self.c.positions(CATEGORY):
            qty = _f(p.get("total"))
            if qty:
                out.append({"symbol": p["symbol"], "side": 1 if p.get("posSide") == "long" else -1, "qty": qty,
                            "avg_price": _f(p.get("avgPrice")), "mark_price": _f(p.get("markPrice"))})
        return out

    def last_price(self, symbol: str) -> tuple[float, float]:
        t = self.c.tickers(CATEGORY, symbol)[0]
        return _f(t["lastPrice"]), _f(t["ts"]) / 1000

    def prepare(self, inst: Instrument, leverage: int) -> None:
        self._check_hold_mode()
        self.c.set_leverage({"category": CATEGORY, "symbol": inst.symbol, "leverage": str(int(leverage)),
                             "marginMode": "isolated"})

    def _place(self, body: dict, oid: str) -> OrderResult:
        res = self.c.place_order(body)
        st = self.order_status(oid)
        return st or OrderResult(oid, "live", exchange_order_id=res.get("orderId"))

    def market_entry_with_stop(self, inst, side, qty, stop, oid) -> OrderResult:
        self._check_hold_mode()
        return self._place({
            "category": CATEGORY, "symbol": inst.symbol, "qty": inst.fmt_qty(qty),
            "side": "buy" if side == 1 else "sell", "orderType": "market", "clientOid": oid,
            "marginMode": "isolated", "reduceOnly": "no",
            "stopLoss": inst.fmt_price(stop), "slTriggerBy": "mark", "slOrderType": "market"}, oid)

    def reduce_limit(self, inst, side, qty, price, oid) -> OrderResult:
        return self._place({
            "category": CATEGORY, "symbol": inst.symbol, "qty": inst.fmt_qty(qty),
            "side": "buy" if side == 1 else "sell", "orderType": "limit", "price": inst.fmt_price(price),
            "timeInForce": "gtc", "clientOid": oid, "marginMode": "isolated", "reduceOnly": "yes"}, oid)

    def flatten(self, inst, side, qty, oid) -> OrderResult:
        # side = side of the POSITION being closed
        return self._place({
            "category": CATEGORY, "symbol": inst.symbol, "qty": inst.fmt_qty(qty),
            "side": "sell" if side == 1 else "buy", "orderType": "market", "clientOid": oid,
            "marginMode": "isolated", "reduceOnly": "yes"}, oid)

    def _stop_order(self, symbol: str) -> dict | None:
        for o in self.c.open_strategy_orders(CATEGORY, "tpsl"):
            if o.get("symbol") == symbol and _f(o.get("stopLoss")) > 0 and o.get("status") in (None, "pending", "submitting"):
                return o
        return None

    def protective_stop(self, symbol: str) -> float | None:
        o = self._stop_order(symbol)
        return _f(o.get("stopLoss")) if o else None

    def move_stop(self, inst, new_stop) -> bool:
        o = self._stop_order(inst.symbol)
        if not o:
            return False
        try:
            self.c.modify_strategy_order({"orderId": o["orderId"], "qty": o.get("qty"),
                                          "stopLoss": inst.fmt_price(new_stop)})
            return True
        except BitgetAPIError:
            return False                     # the original (wider) stop stays in place

    def cancel(self, inst, oid) -> bool:
        try:
            self.c.cancel_order({"clientOid": oid, "category": CATEGORY})
            return True
        except BitgetAPIError:
            return False

    def order_status(self, oid) -> OrderResult | None:
        try:
            o = self.c.order_info(client_oid=oid)
        except BitgetAPIError:
            return None
        if not o:
            return None
        fee = sum(abs(_f(x.get("fee"))) for x in o.get("feeDetail") or [])
        return OrderResult(oid, STATUS_MAP.get(str(o.get("orderStatus")), "live"), _f(o.get("cumExecQty")),
                           _f(o.get("avgPrice")), fee, o.get("orderId"))

    def fills(self, symbol, since_ms) -> list[Fill]:
        out = []
        for f in self.c.fills(CATEGORY):
            if f.get("symbol") != symbol or int(f.get("createdTime") or 0) < since_ms:
                continue
            fee = sum(abs(_f(x.get("fee"))) for x in f.get("feeDetail") or [])
            out.append(Fill(str(f.get("execId")), symbol, f.get("clientOid") or None, str(f.get("side")),
                            _f(f.get("execPrice")), _f(f.get("execQty")), fee, int(f.get("createdTime") or 0),
                            str(f.get("tradeSide")) == "close"))
        return out
