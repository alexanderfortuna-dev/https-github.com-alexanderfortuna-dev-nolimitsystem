"""Execution engine: turns an APPROVED TradePlan into orders on a paper/demo broker.

Lifecycle:  ARMED -> ENTRY_SUBMITTING -> OPEN -> TP1_DONE -> CLOSED
            (or CANCELLED / EXPIRED / REJECTED)

Safety properties (each covered by a test in tests/test_execution.py):
  * Trigger entry: armed plans fire only when a FRESH price crosses the trigger, and not
    when price has already run more than CHASE_LIMIT_R beyond it.
  * Atomic protection: the entry carries its stop. If no stop can be confirmed after the
    fill, the position is flattened immediately.
  * Duplicate prevention: one trade per signal (primary key), deterministic clientOid per
    order, order row written BEFORE the request is sent. After a transport error the order
    is reconciled by clientOid; it is never blindly re-sent.
  * Exits are reduce-only. Partial profit at TP1, then the stop moves to entry.
  * Reconciliation: fills are booked once (exec_id unique); positions are compared with
    the book every tick; a mismatch engages the kill switch.
  * Kill switch: cancels armed entries, blocks new ones, optionally flattens positions
    opened by this system. Positions the engine did not open are never touched.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from ..bitget.models import Instrument
from ..config import RiskLimits
from ..data import INTERVAL_MS
from ..risk import AccountState, KillSwitch, PositionView, RiskEngine, TradePlan
from ..store import DuplicateKey, Store, now_iso
from ..strategies import MAX_HOLD_BARS
from .broker import Broker, OrderResult, client_oid

log = logging.getLogger("aplus.exec")

ACTIVE = ("ARMED", "ENTRY_SUBMITTING", "OPEN", "TP1_DONE")
CHASE_LIMIT_R = 0.25           # do not enter if price is already this far (in R) beyond the trigger
SUBMIT_GRACE_S = 60            # after this, an entry order unknown to the exchange is declared not sent


class ExecutionEngine:
    def __init__(self, broker: Broker, store: Store, risk: RiskEngine, kill: KillSwitch,
                 instruments: dict[str, Instrument], limits: RiskLimits, clock=time.time):
        if broker.mode not in ("paper", "demo"):
            raise ValueError("only paper and demo brokers are supported; production trading is disabled")
        self.b, self.s, self.risk, self.kill = broker, store, risk, kill
        self.insts, self.limits, self.clock = instruments, limits, clock
        self.mode = broker.mode

    # ------------------------------------------------------------------ helpers
    def _ev(self, level: str, kind: str, msg: str, **data) -> None:
        log.log(getattr(logging, level.upper(), logging.INFO), "%s: %s %s", kind, msg, data or "")
        self.s.event(level, kind, msg, {"mode": self.mode, **data})

    def _active(self) -> list[dict]:
        return [t for t in self.s.select("trades", {"mode": self.mode}) if t["state"] in ACTIVE]

    def _set(self, trade: dict, **values) -> None:
        values["updated_at"] = now_iso()
        self.s.update("trades", {"trade_key": trade["trade_key"]}, values)
        trade.update(values)

    def _order_row(self, trade: dict, role: str, side: int, qty: float, price=None, trigger=None) -> str | None:
        """Write the order intent first. Returns the clientOid, or None if it already exists."""
        oid = client_oid(trade["trade_key"], role)
        try:
            self.s.insert("orders", {"client_oid": oid, "trade_key": trade["trade_key"], "role": role,
                                     "status": "SUBMITTING", "side": "buy" if side == 1 else "sell", "qty": qty,
                                     "price": price, "trigger_price": trigger, "reduce_only": role != "entry",
                                     "detail": {}, "created_at": now_iso(), "updated_at": now_iso()})
        except DuplicateKey:
            self._ev("warning", "duplicate_order_blocked", f"{role} order already recorded", trade=trade["trade_key"], oid=oid)
            return None
        return oid

    def _order_update(self, oid: str, res: OrderResult) -> None:
        self.s.update("orders", {"client_oid": oid}, {"status": res.status, "exchange_order_id": res.exchange_order_id,
                                                      "detail": {"filled_qty": res.filled_qty, "avg_price": res.avg_price,
                                                                 "fee": res.fee, "message": res.message},
                                                      "updated_at": now_iso()})

    # ------------------------------------------------------------------ account view for the risk engine
    def account_state(self, snapshot: bool = True) -> AccountState:
        now = self.clock()
        acct = self.b.account()
        issues = list(acct.get("issues") or [])
        complete = bool(acct.get("complete"))
        active = self._active()
        managed = {t["symbol"] for t in active if t["state"] in ("OPEN", "TP1_DONE", "ENTRY_SUBMITTING")}
        positions: list[PositionView] = []
        try:
            for p in self.b.positions():
                positions.append(PositionView(p["symbol"], p["side"], p["qty"], p["mark_price"], p["symbol"] in managed))
        except Exception as exc:  # noqa: BLE001
            complete = False
            issues.append(f"positions: {exc}")
        open_risk = sum(float(t["plan"]["planned_risk"]) for t in active if t["state"] in ("ARMED", "ENTRY_SUBMITTING", "OPEN"))
        dt = datetime.fromtimestamp(now, timezone.utc)
        day0 = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        week0 = day0 - timedelta(days=day0.weekday())
        equity = float(acct.get("equity") or 0.0)
        st = AccountState(equity=equity, available=float(acct.get("available") or 0.0), ts=now, complete=complete,
                          issues=issues, positions=positions, open_planned_risk=open_risk,
                          pending_entry_symbols={t["symbol"] for t in active if t["state"] in ("ARMED", "ENTRY_SUBMITTING")})
        if snapshot:
            self.s.insert("account_snapshots", {
                "ts": dt.isoformat(timespec="milliseconds"), "mode": self.mode, "equity": equity,
                "available": st.available, "unrealized_pnl": 0.0, "gross_exposure": st.gross_exposure,
                "liabilities": 0.0, "complete": complete, "issues": issues,
                "detail": {"open_planned_risk": open_risk, "positions": [p.__dict__ for p in positions]}})
        st.day_start_equity = self.s.period_start_equity(self.mode, day0.isoformat(timespec="milliseconds"))
        st.week_start_equity = self.s.period_start_equity(self.mode, week0.isoformat(timespec="milliseconds"))
        return st

    # ------------------------------------------------------------------ submit
    def submit(self, plan: TradePlan, candidate_key: str, expires_ms: int) -> str:
        """Arm an approved plan. Returns ARMED, or DUPLICATE if this signal was already taken."""
        if self.kill.active:
            self._ev("warning", "submit_blocked", "kill switch active", candidate=candidate_key)
            return "BLOCKED"
        key = f"{self.mode}|{candidate_key}"
        d = plan.to_dict()
        d["expires_ms"] = int(expires_ms)
        try:
            self.s.insert("trades", {"trade_key": key, "mode": self.mode, "symbol": plan.symbol, "strategy": plan.strategy,
                                     "interval": plan.interval, "side": plan.side, "signal_ts": plan.signal_ts,
                                     "state": "ARMED", "plan": d, "stop_current": plan.stop,
                                     "created_at": now_iso(), "updated_at": now_iso()})
        except DuplicateKey:
            self._ev("info", "duplicate_trade_blocked", "signal already has a trade", trade=key)
            return "DUPLICATE"
        self._ev("info", "armed", f"{plan.symbol} {'LONG' if plan.side == 1 else 'SHORT'} trigger {plan.entry}", trade=key)
        return "ARMED"

    # ------------------------------------------------------------------ main loop step
    def tick(self) -> None:
        for t in self._active():
            try:
                {"ARMED": self._tick_armed, "ENTRY_SUBMITTING": self._tick_submitting,
                 "OPEN": self._tick_open, "TP1_DONE": self._tick_open}[t["state"]](t)
            except Exception as exc:  # noqa: BLE001 - one trade must not stop the others
                self._ev("error", "tick_error", f"{type(exc).__name__}: {exc}", trade=t["trade_key"])
        self.s.heartbeat(f"engine:{self.mode}", "ok", {"active_trades": len(self._active()),
                                                         "kill_switch": self.kill.active})

    def _tick_armed(self, t: dict) -> None:
        plan, now = t["plan"], self.clock()
        if self.kill.active:
            self._set(t, state="CANCELLED", exit_reason="kill_switch", closed_at=now_iso())
            self._ev("warning", "entry_cancelled", "kill switch", trade=t["trade_key"])
            return
        if now * 1000 > plan["expires_ms"]:
            self._set(t, state="EXPIRED", exit_reason="trigger_not_reached", closed_at=now_iso())
            return
        price, pts = self.b.last_price(t["symbol"])
        if now - pts > self.limits.max_ticker_age_s:
            self._ev("warning", "stale_data_rejected", f"quote is {now - pts:.1f}s old", trade=t["trade_key"])
            return
        side, entry, stop = t["side"], plan["entry"], plan["stop"]
        r_unit = abs(entry - stop)
        if (price - entry) * side < 0:
            return                                       # trigger not reached
        if (price - stop) * side <= 0:
            self._set(t, state="CANCELLED", exit_reason="price_beyond_stop", closed_at=now_iso())
            return
        if (price - entry) * side > CHASE_LIMIT_R * r_unit:
            return                                       # ran away: never chase
        acct = self.account_state(snapshot=False)
        blocks = list(self.risk.circuit_breakers(acct))
        if not acct.complete:
            blocks.append("account_incomplete")
        if any(p.symbol == t["symbol"] and p.qty for p in acct.positions):
            blocks.append("existing_position_on_symbol")
        if blocks:
            self._set(t, state="CANCELLED", exit_reason=";".join(blocks), closed_at=now_iso())
            self._ev("warning", "entry_cancelled", ";".join(blocks), trade=t["trade_key"])
            return
        inst = self.insts[t["symbol"]]
        oid = self._order_row(t, "entry", side, plan["qty"], trigger=entry)
        self._set(t, state="ENTRY_SUBMITTING")
        if oid is None:
            return                                       # already recorded: next tick reconciles by clientOid
        try:
            self.b.prepare(inst, int(plan["exchange_leverage"]))
            res = self.b.market_entry_with_stop(inst, side, plan["qty"], stop, oid)
        except Exception as exc:  # noqa: BLE001
            # outcome unknown: do NOT resend. _tick_submitting resolves it by clientOid.
            self._ev("error", "entry_submit_error", f"{type(exc).__name__}: {exc}", trade=t["trade_key"], oid=oid)
            return
        self._on_entry_result(t, oid, res)

    def _tick_submitting(self, t: dict) -> None:
        oid = client_oid(t["trade_key"], "entry")
        res = self.b.order_status(oid)
        if res is None:
            row = self.s.one("orders", {"client_oid": oid})
            age = self.clock() - datetime.fromisoformat(row["created_at"]).timestamp() if row else SUBMIT_GRACE_S + 1
            if age > SUBMIT_GRACE_S:
                self.s.update("orders", {"client_oid": oid}, {"status": "not_found", "updated_at": now_iso()})
                self._set(t, state="REJECTED", exit_reason="entry_not_found_on_exchange", closed_at=now_iso())
                self._ev("warning", "entry_unconfirmed", "order unknown to exchange after grace period", trade=t["trade_key"])
            return
        self._on_entry_result(t, oid, res)

    def _on_entry_result(self, t: dict, oid: str, res: OrderResult) -> None:
        self._order_update(oid, res)
        plan, inst, side = t["plan"], self.insts[t["symbol"]], t["side"]
        if res.status in ("rejected", "cancelled") and res.filled_qty <= 0:
            self._set(t, state="REJECTED", exit_reason=f"entry_{res.status}: {res.message}"[:200], closed_at=now_iso())
            self._ev("warning", "entry_rejected", res.message, trade=t["trade_key"])
            return
        if res.filled_qty <= 0:
            return                                       # still working
        qty = res.filled_qty
        self._set(t, state="OPEN", qty_open=qty, entry_fill=res.avg_price, opened_at=now_iso())
        self._ev("info", "entry_filled", f"{t['symbol']} {qty} @ {res.avg_price}", trade=t["trade_key"])
        # --- no protective risk control = no position
        if not self._ensure_stop(t, plan["stop"]):
            return
        tp1_qty = inst.round_qty_down(qty * (plan["tp1_qty"] / plan["qty"])) if plan["tp1_qty"] else 0.0
        tp2_qty = inst.round_qty_down(qty - tp1_qty)
        for role, q, px in (("tp1", tp1_qty, plan["tp1"]), ("tp2", tp2_qty, plan["tp2"])):
            if q <= 0:
                continue
            o = self._order_row(t, role, -side, q, price=px)
            if o is None:
                continue
            try:
                r = self.b.reduce_limit(inst, -side, q, px, o)
            except Exception as exc:  # noqa: BLE001
                r = OrderResult(o, "rejected", message=f"{type(exc).__name__}: {exc}")
            self._order_update(o, r)
            if r.status == "rejected":
                # position stays protected by its stop; the time stop still applies
                self._ev("warning", "tp_rejected", f"{role}: {r.message}", trade=t["trade_key"])

    def _ensure_stop(self, t: dict, stop: float) -> bool:
        inst = self.insts[t["symbol"]]
        if self.b.protective_stop(t["symbol"]) is not None:
            return True
        if self.b.move_stop(inst, stop) and self.b.protective_stop(t["symbol"]) is not None:
            self._ev("warning", "stop_restored", "protective stop was missing and has been re-placed", trade=t["trade_key"])
            return True
        self._ev("error", "no_protective_stop", "flattening unprotected position", trade=t["trade_key"])
        self._flatten(t, "no_protective_stop")
        return False

    def _flatten(self, t: dict, reason: str) -> None:
        inst = self.insts[t["symbol"]]
        for role in ("tp1", "tp2"):
            self.b.cancel(inst, client_oid(t["trade_key"], role))
        pos = next((p for p in self.b.positions() if p["symbol"] == t["symbol"]), None)
        if pos:
            oid = self._order_row(t, "flatten", -t["side"], pos["qty"])
            if oid is None:
                oid = client_oid(t["trade_key"], "flatten")
                if self.b.order_status(oid) is not None:
                    return                               # flatten already sent
            try:
                res = self.b.flatten(inst, t["side"], pos["qty"], oid)
                self._order_update(oid, res)
            except Exception as exc:  # noqa: BLE001
                self._ev("error", "flatten_error", f"{type(exc).__name__}: {exc}", trade=t["trade_key"])
                self.kill.engage(f"flatten failed for {t['symbol']}: manual action required")
                return
            if res.status == "rejected":
                self._ev("error", "flatten_rejected", res.message, trade=t["trade_key"])
                self.kill.engage(f"flatten rejected for {t['symbol']}: manual action required")
                return
        self._set(t, exit_reason=reason)

    def _book_fills(self, t: dict) -> None:
        opened_ms = int(datetime.fromisoformat(t["opened_at"]).timestamp() * 1000) - 5_000
        roles = {client_oid(t["trade_key"], r): r for r in ("entry", "tp1", "tp2", "flatten")}
        for f in self.b.fills(t["symbol"], opened_ms):
            role = roles.get(f.client_oid or "", "stop" if f.closing else "unknown")
            try:
                self.s.insert("fills", {"ts": now_iso(), "exec_id": f"{self.mode}:{f.exec_id}", "client_oid": f.client_oid,
                                        "trade_key": t["trade_key"], "role": role, "price": f.price, "qty": f.qty,
                                        "fee": f.fee})
            except DuplicateKey:
                pass                                     # already booked

    def _tick_open(self, t: dict) -> None:
        inst, plan = self.insts[t["symbol"]], t["plan"]
        if hasattr(self.b, "process"):
            self.b.process(t["symbol"])                  # paper simulator step
        self._book_fills(t)
        pos = next((p for p in self.b.positions() if p["symbol"] == t["symbol"]), None)
        if pos is None or not pos["qty"]:
            self._finalize(t)
            return
        booked = self.s.select("fills", {"trade_key": t["trade_key"]})
        expected = sum(f["qty"] for f in booked if f["role"] == "entry") - sum(f["qty"] for f in booked if f["role"] != "entry")
        if abs(pos["qty"] - expected) > inst.qty_step / 2 or pos["side"] != t["side"]:
            self._ev("error", "position_mismatch", f"exchange {pos['qty']} vs book {expected}", trade=t["trade_key"])
            self.kill.engage(f"position mismatch on {t['symbol']}: exchange {pos['qty']} vs book {expected}")
            return
        self._set(t, qty_open=pos["qty"])
        if t.get("exit_reason"):                         # a flatten is already in flight
            return
        if not self._ensure_stop(t, t["stop_current"]):
            return
        if t["state"] == "OPEN":
            st = self.b.order_status(client_oid(t["trade_key"], "tp1"))
            if st and st.status == "filled":
                self._order_update(st.client_oid, st)
                moved = self.b.move_stop(inst, inst.round_price(t["entry_fill"]))
                self._set(t, state="TP1_DONE", stop_current=t["entry_fill"] if moved else t["stop_current"])
                self._ev("info" if moved else "warning", "tp1_filled",
                         "stop moved to entry" if moved else "stop move FAILED; original stop kept", trade=t["trade_key"])
        held_ms = self.clock() * 1000 - datetime.fromisoformat(t["opened_at"]).timestamp() * 1000
        if held_ms > MAX_HOLD_BARS * INTERVAL_MS[t["interval"]]:
            self._flatten(t, "time_exit")
        elif self.kill.active and "flatten" in self.kill.reason():
            self._flatten(t, "kill_switch")

    def _finalize(self, t: dict) -> None:
        inst, plan = self.insts[t["symbol"]], t["plan"]
        for role in ("tp1", "tp2"):
            oid = client_oid(t["trade_key"], role)
            st = self.b.order_status(oid)
            if st:
                if st.status == "live":
                    self.b.cancel(inst, oid)
                    st.status = "cancelled"
                self._order_update(oid, st)
        fills = self.s.select("fills", {"trade_key": t["trade_key"]})
        entries = [f for f in fills if f["role"] == "entry"]
        exits = [f for f in fills if f["role"] != "entry"]
        q_in = sum(f["qty"] for f in entries)
        avg_in = sum(f["price"] * f["qty"] for f in entries) / q_in if q_in else t["entry_fill"]
        pnl = sum((f["price"] - avg_in) * t["side"] * f["qty"] for f in exits)
        fees = sum(f["fee"] for f in fills)
        roles = {f["role"] for f in exits}
        reason = t.get("exit_reason") or ("tp2" if "tp2" in roles and "stop" not in roles else
                                          "breakeven_stop" if t["state"] == "TP1_DONE" else "stop")
        net_r = (pnl - fees) / plan["planned_risk"] if plan["planned_risk"] else 0.0
        self._set(t, state="CLOSED", qty_open=0.0, realized_pnl=pnl, fees=fees, net_r=net_r, exit_reason=reason,
                  closed_at=now_iso())
        self._ev("info", "trade_closed", f"{t['symbol']} {reason} net {net_r:+.2f}R", trade=t["trade_key"],
                 pnl=pnl, fees=fees)

    # ------------------------------------------------------------------ reconciliation / emergency
    def reconcile(self) -> list[str]:
        """Compare exchange state with the book. Returns human-readable findings."""
        findings = []
        active = {t["symbol"]: t for t in self._active() if t["state"] in ("OPEN", "TP1_DONE", "ENTRY_SUBMITTING")}
        for p in self.b.positions():
            if p["symbol"] not in active:
                findings.append(f"EXTERNAL position {p['symbol']} qty {p['qty']}: not opened by this system, left untouched")
            elif self.b.protective_stop(p["symbol"]) is None:
                findings.append(f"UNPROTECTED managed position {p['symbol']}: will be fixed or flattened on next tick")
        held = {p["symbol"] for p in self.b.positions()}
        for sym, t in active.items():
            if t["state"] != "ENTRY_SUBMITTING" and sym not in held:
                findings.append(f"managed trade {t['trade_key']} has no exchange position: will be finalized on next tick")
        for f in findings:
            self._ev("warning", "reconcile", f)
        self.s.heartbeat(f"reconcile:{self.mode}", "ok" if not findings else "findings", {"findings": findings})
        return findings

    def emergency_stop(self, reason: str, flatten: bool = False) -> None:
        self.kill.engage(reason + (" [flatten]" if flatten else ""))
        self._ev("error", "kill_switch", reason, flatten=flatten)
        self.tick()
