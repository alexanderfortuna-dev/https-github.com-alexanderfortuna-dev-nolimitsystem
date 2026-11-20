"""Broker abstraction. Two implementations exist: PaperBroker (local simulator) and
DemoBroker (Bitget Demo Trading). There is intentionally no production broker."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..bitget.models import Instrument


def client_oid(trade_key: str, role: str) -> str:
    """Deterministic, <=32 chars, Bitget-safe. Same trade+role always maps to the same id,
    so a repeated submission collides instead of creating a second order."""
    return "ap" + hashlib.sha1(f"{trade_key}|{role}".encode()).hexdigest()[:26] + role[:1] + role[-1:]


@dataclass
class OrderResult:
    client_oid: str
    status: str                 # live | filled | partially_filled | cancelled | rejected
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0
    exchange_order_id: str | None = None
    message: str = ""


@dataclass
class Fill:
    exec_id: str
    symbol: str
    client_oid: str | None
    side: str                   # buy | sell
    price: float
    qty: float
    fee: float
    ts_ms: int
    closing: bool


class BrokerError(RuntimeError):
    pass


class Broker(Protocol):
    mode: str
    def account(self) -> dict: ...                       # {"equity","available","complete","issues"}
    def positions(self) -> list[dict]: ...               # [{"symbol","side","qty","avg_price","mark_price"}]
    def last_price(self, symbol: str) -> tuple[float, float]: ...   # (price, unix_ts_of_quote)
    def prepare(self, inst: Instrument, leverage: int) -> None: ...
    def market_entry_with_stop(self, inst: Instrument, side: int, qty: float, stop: float, oid: str) -> OrderResult: ...
    def reduce_limit(self, inst: Instrument, side: int, qty: float, price: float, oid: str) -> OrderResult: ...
    def flatten(self, inst: Instrument, side: int, qty: float, oid: str) -> OrderResult: ...
    def move_stop(self, inst: Instrument, new_stop: float) -> bool: ...
    def protective_stop(self, symbol: str) -> float | None: ...
    def cancel(self, inst: Instrument, oid: str) -> bool: ...
    def order_status(self, oid: str) -> OrderResult | None: ...
    def fills(self, symbol: str, since_ms: int) -> list[Fill]: ...


# =============================================================================== paper
@dataclass
class _PaperPos:
    side: int
    qty: float
    avg_price: float
    stop: float | None


@dataclass
class _PaperOrder:
    oid: str
    symbol: str
    side: int           # order side: +1 buy / -1 sell
    qty: float
    price: float
    status: str = "live"
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0


class PaperBroker:
    """Deterministic simulator. Prices come from `price_fn(symbol) -> (price, ts)`.
    Stops fill at the stop price worsened by slippage; reduce-only limits fill at their price
    once the market trades through them. Fees use the instrument's taker/maker rates."""
    mode = "paper"

    def __init__(self, price_fn: Callable[[str], tuple[float, float]], starting_equity: float = 10_000.0,
                 slippage: float = 0.0002, fail_next: dict | None = None):
        self._price_fn = price_fn
        self.cash = starting_equity
        self.slippage = slippage
        self.pos: dict[str, _PaperPos] = {}
        self.orders: dict[str, _PaperOrder] = {}
        self._fills: list[Fill] = []
        self._insts: dict[str, Instrument] = {}
        self._seq = 0
        # fault injection for failure simulations: {"entry": "timeout"|"reject"|"no_stop", "reduce_limit": "reject", ...}
        self.fail_next: dict = fail_next or {}

    # ---- persistence (paper state survives process restarts)
    def save(self, path) -> None:
        import json
        from dataclasses import asdict
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "cash": self.cash, "seq": self._seq, "pos": {k: asdict(v) for k, v in self.pos.items()},
            "orders": {k: asdict(v) for k, v in self.orders.items()}, "fills": [asdict(f) for f in self._fills[-2000:]]}))

    def load(self, path) -> bool:
        import json
        from pathlib import Path
        if not Path(path).exists():
            return False
        d = json.loads(Path(path).read_text())
        self.cash, self._seq = d["cash"], d["seq"]
        self.pos = {k: _PaperPos(**v) for k, v in d["pos"].items()}
        self.orders = {k: _PaperOrder(**v) for k, v in d["orders"].items()}
        self._fills = [Fill(**f) for f in d["fills"]]
        return True

    # ---- helpers
    def _fill(self, symbol: str, oid: str | None, side: int, price: float, qty: float, fee: float, closing: bool) -> None:
        self._seq += 1
        self._fills.append(Fill(f"paper-{self._seq}", symbol, oid, "buy" if side == 1 else "sell", price, qty, fee,
                                int(time.time() * 1000), closing))
        self.cash -= fee

    def _close(self, symbol: str, qty: float, price: float, oid: str | None, fee_rate: float) -> None:
        p = self.pos[symbol]
        qty = min(qty, p.qty)
        self.cash += (price - p.avg_price) * p.side * qty
        self._fill(symbol, oid, -p.side, price, qty, price * qty * fee_rate, True)
        p.qty = round(p.qty - qty, 12)
        if p.qty <= 1e-12:
            del self.pos[symbol]
            for o in self.orders.values():           # exchange behaviour: reduce-only orders die with the position
                if o.symbol == symbol and o.status == "live":
                    o.status = "cancelled"

    def process(self, symbol: str) -> None:
        """Advance the simulation for one symbol using the current price."""
        if symbol not in self.pos:
            return
        price, _ = self._price_fn(symbol)
        inst, p = self._insts[symbol], self.pos[symbol]
        if p.stop is not None and (price - p.stop) * p.side <= 0:
            px = p.stop * (1 - p.side * self.slippage * 1.5)
            self._close(symbol, p.qty, px, None, inst.taker_fee)
            return
        for o in list(self.orders.values()):
            if o.symbol != symbol or o.status != "live" or symbol not in self.pos:
                continue
            if (price - o.price) * (-o.side) > 0:    # traded through the limit
                q = min(o.qty, self.pos[symbol].qty)
                o.status, o.filled_qty, o.avg_price = "filled", q, o.price
                o.fee = o.price * q * inst.maker_fee
                self._close(symbol, q, o.price, o.oid, inst.maker_fee)

    # ---- Broker interface
    def account(self) -> dict:
        upnl = 0.0
        for s, p in self.pos.items():
            upnl += (self._price_fn(s)[0] - p.avg_price) * p.side * p.qty
        margin = sum(p.qty * p.avg_price for p in self.pos.values()) / 10
        return {"equity": self.cash + upnl, "available": max(self.cash + min(upnl, 0) - margin, 0.0),
                "complete": True, "issues": []}

    def positions(self) -> list[dict]:
        return [{"symbol": s, "side": p.side, "qty": p.qty, "avg_price": p.avg_price,
                 "mark_price": self._price_fn(s)[0]} for s, p in self.pos.items()]

    def last_price(self, symbol: str) -> tuple[float, float]:
        return self._price_fn(symbol)

    def prepare(self, inst: Instrument, leverage: int) -> None:
        self._insts[inst.symbol] = inst

    def market_entry_with_stop(self, inst, side, qty, stop, oid) -> OrderResult:
        if oid in self.orders:
            raise BrokerError("25212 Duplicate clientOid")
        fault = self.fail_next.pop("entry", None)
        if fault == "reject":
            return OrderResult(oid, "rejected", message="simulated rejection")
        self._insts[inst.symbol] = inst
        price, _ = self._price_fn(inst.symbol)
        px = price * (1 + side * self.slippage)
        fee = px * qty * inst.taker_fee
        self.pos[inst.symbol] = _PaperPos(side, qty, px, None if fault == "no_stop" else stop)
        self.orders[oid] = _PaperOrder(oid, inst.symbol, side, qty, px, "filled", qty, px, fee)
        self._fill(inst.symbol, oid, side, px, qty, fee, False)
        if fault == "timeout":                       # order was accepted but the response was lost
            raise TimeoutError("simulated timeout after order acceptance")
        return OrderResult(oid, "filled", qty, px, fee, f"paper-{oid}")

    def reduce_limit(self, inst, side, qty, price, oid) -> OrderResult:
        if oid in self.orders:
            raise BrokerError("25212 Duplicate clientOid")
        if self.fail_next.pop("reduce_limit", None) == "reject":
            return OrderResult(oid, "rejected", message="simulated rejection")
        p = self.pos.get(inst.symbol)
        if not p or p.side == side:
            return OrderResult(oid, "rejected", message="reduce-only: no opposite position")
        self.orders[oid] = _PaperOrder(oid, inst.symbol, side, qty, price)
        return OrderResult(oid, "live", exchange_order_id=f"paper-{oid}")

    def flatten(self, inst, side, qty, oid) -> OrderResult:
        p = self.pos.get(inst.symbol)
        if not p:
            return OrderResult(oid, "rejected", message="no position")
        if self.fail_next.pop("flatten", None) == "reject":
            return OrderResult(oid, "rejected", message="simulated rejection")
        price, _ = self._price_fn(inst.symbol)
        px = price * (1 - p.side * self.slippage)
        q = min(qty, p.qty)
        fee = px * q * inst.taker_fee
        self.orders[oid] = _PaperOrder(oid, inst.symbol, -p.side, q, px, "filled", q, px, fee)
        self._close(inst.symbol, q, px, oid, inst.taker_fee)
        return OrderResult(oid, "filled", q, px, fee, f"paper-{oid}")

    def move_stop(self, inst, new_stop) -> bool:
        if self.fail_next.pop("move_stop", None) == "reject":
            return False
        p = self.pos.get(inst.symbol)
        if not p:
            return False
        p.stop = new_stop
        return True

    def protective_stop(self, symbol) -> float | None:
        p = self.pos.get(symbol)
        return p.stop if p else None

    def cancel(self, inst, oid) -> bool:
        o = self.orders.get(oid)
        if o and o.status == "live":
            o.status = "cancelled"
            return True
        return False

    def order_status(self, oid) -> OrderResult | None:
        o = self.orders.get(oid)
        if not o:
            return None
        return OrderResult(oid, o.status, o.filled_qty, o.avg_price, o.fee, f"paper-{oid}")

    def fills(self, symbol, since_ms) -> list[Fill]:
        return [f for f in self._fills if f.symbol == symbol and f.ts_ms >= since_ms]
