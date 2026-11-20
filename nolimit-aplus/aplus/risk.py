"""Risk engine. Every entry must be approved here; there is no bypass path.

Key distinction kept explicit everywhere:
  * exchange_leverage  = the leverage SETTING on Bitget (only determines margin posted
                         and where liquidation sits).
  * effective_leverage = position notional / account equity (what actually drives P&L).
A 20x setting with a 0.3x effective leverage is a small position; the engine sizes from
the STOP distance and reports both numbers, never one in place of the other.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .bitget.models import Instrument
from .config import RiskLimits
from .strategies import TP1_FRACTION, TP1_R, TP2_R

ASSUMED_MMR = 0.02            # conservative maintenance-margin rate when tiers are not loaded
MAX_EXCHANGE_LEVERAGE = 20    # engine never selects a setting above this, whatever the contract allows
MAX_MARGIN_SHARE = 0.5        # one trade may lock at most this share of available balance


@dataclass(frozen=True)
class PositionView:
    symbol: str
    side: int                 # +1 long / -1 short
    qty: float
    mark_price: float
    managed: bool             # True only for positions opened by this system
    margin_mode: str = ""
    exchange_leverage: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.mark_price


@dataclass
class AccountState:
    equity: float
    available: float
    ts: float                                 # unix seconds of the snapshot
    complete: bool                            # False if any required account view failed to load
    issues: list[str] = field(default_factory=list)
    positions: list[PositionView] = field(default_factory=list)
    open_planned_risk: float = 0.0            # sum of planned risk of system trades still open/pending (USDT)
    day_start_equity: float | None = None
    week_start_equity: float | None = None
    pending_entry_symbols: set[str] = field(default_factory=set)

    @property
    def gross_exposure(self) -> float:
        return sum(p.notional for p in self.positions)

    @property
    def gross_leverage(self) -> float:
        return self.gross_exposure / self.equity if self.equity > 0 else math.inf


@dataclass
class TradePlan:
    symbol: str
    strategy: str
    interval: str
    side: int
    signal_ts: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    qty: float
    tp1_qty: float
    tp2_qty: float
    notional: float
    planned_risk: float            # USDT lost if stopped, incl. fees and slippage
    risk_pct: float
    effective_leverage: float      # this trade: notional / equity
    portfolio_leverage_after: float
    exchange_leverage: int
    margin_mode: str
    margin_required: float
    est_liq_price: float
    est_fees: float
    expected_net_rr: float         # net reward if TP1+TP2 fill / net loss if stopped

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str]
    plan: TradePlan | None = None
    checks: dict = field(default_factory=dict)


class KillSwitch:
    """File-backed emergency stop. Present file = no new entries, pending entries are cancelled."""

    def __init__(self, path: Path):
        self.path = path

    @property
    def active(self) -> bool:
        return self.path.exists()

    def reason(self) -> str:
        return self.path.read_text().strip() if self.active else ""

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{int(time.time())} {reason}\n")

    def release(self) -> None:
        if self.path.exists():
            self.path.unlink()


class RiskEngine:
    def __init__(self, limits: RiskLimits, kill_switch: KillSwitch | None = None):
        self.limits = limits
        self.kill = kill_switch

    # ------------------------------------------------------------ circuit breakers
    def circuit_breakers(self, acct: AccountState) -> list[str]:
        out = []
        if acct.day_start_equity and acct.day_start_equity > 0:
            dd = (acct.equity - acct.day_start_equity) / acct.day_start_equity
            if dd <= -self.limits.daily_loss_limit:
                out.append(f"daily_loss_breaker: {dd:.2%} <= -{self.limits.daily_loss_limit:.2%}")
        if acct.week_start_equity and acct.week_start_equity > 0:
            dd = (acct.equity - acct.week_start_equity) / acct.week_start_equity
            if dd <= -self.limits.weekly_loss_limit:
                out.append(f"weekly_loss_breaker: {dd:.2%} <= -{self.limits.weekly_loss_limit:.2%}")
        return out

    # ------------------------------------------------------------ sizing
    def evaluate(self, *, inst: Instrument, strategy: str, interval: str, side: int, signal_ts: int,
                 entry: float, stop: float, acct: AccountState, slippage: float,
                 ticker_age_s: float, validation_status: str, now: float | None = None) -> RiskDecision:
        now = now or time.time()
        L = self.limits
        reasons: list[str] = []
        checks: dict = {}

        # --- hard pre-conditions: no verified data / no validated edge / no control -> no trade
        if self.kill and self.kill.active:
            reasons.append(f"kill_switch_active: {self.kill.reason()}")
        if not acct.complete:
            reasons.append("account_incomplete: " + "; ".join(acct.issues or ["unknown"]))
        if now - acct.ts > L.max_account_age_s:
            reasons.append(f"account_snapshot_stale: {now - acct.ts:.0f}s")
        if ticker_age_s > L.max_ticker_age_s:
            reasons.append(f"ticker_stale: {ticker_age_s:.1f}s")
        if validation_status not in ("OOS_PASSED_FORWARD_PENDING", "VALIDATED"):
            reasons.append(f"edge_not_validated: {validation_status}")
        if inst.status != "online":
            reasons.append(f"instrument_status: {inst.status}")
        if acct.equity <= 0:
            reasons.append("equity_not_positive")
        if side not in (1, -1) or not all(math.isfinite(x) and x > 0 for x in (entry, stop)):
            reasons.append("invalid_prices")
        elif (entry - stop) * side <= 0:
            reasons.append("stop_on_wrong_side")
        reasons += self.circuit_breakers(acct)
        # never add to or alter an existing position (user's or ours): no averaging down, no pyramiding
        if any(p.symbol == inst.symbol and p.qty != 0 for p in acct.positions):
            reasons.append("existing_position_on_symbol")
        if inst.symbol in acct.pending_entry_symbols:
            reasons.append("pending_entry_on_symbol")
        if reasons:
            return RiskDecision(False, reasons, None, checks)

        # --- size from stop distance
        risk_budget = L.risk_per_trade * acct.equity
        remaining_risk = L.max_open_risk * acct.equity - acct.open_planned_risk
        checks["risk_budget"] = risk_budget
        checks["remaining_open_risk"] = remaining_risk
        if remaining_risk < risk_budget * 0.999:
            return RiskDecision(False, [f"open_risk_cap: remaining {remaining_risk:.2f} < per-trade {risk_budget:.2f}"],
                                None, checks)
        entry_fill = entry * (1 + side * slippage)
        stop_fill = stop * (1 - side * slippage * 1.5)
        loss_per_unit = (entry_fill - stop_fill) * side + entry_fill * inst.taker_fee + stop_fill * inst.taker_fee
        qty = risk_budget / loss_per_unit
        # --- gross exposure cap (includes the owner's existing positions, which are never modified)
        exposure_room = L.max_gross_exposure * acct.equity - acct.gross_exposure
        checks["exposure_room"] = exposure_room
        if exposure_room <= 0:
            return RiskDecision(False, [f"gross_exposure_cap: {acct.gross_leverage:.2f}x >= {L.max_gross_exposure}x"],
                                None, checks)
        qty = min(qty, exposure_room / entry, inst.max_qty, inst.max_market_qty)
        qty = inst.round_qty_down(qty)
        notional = qty * entry
        if qty <= 0 or qty < inst.min_qty or notional < inst.min_notional:
            return RiskDecision(False, [f"below_minimum_order: qty={qty} notional={notional:.2f}"], None, checks)

        # --- exchange leverage SETTING: low enough that liquidation sits well beyond the stop
        stop_pct = abs(entry - stop) / entry
        lev = int(min(inst.max_leverage, MAX_EXCHANGE_LEVERAGE,
                      math.floor(1.0 / (L.liq_buffer_mult * stop_pct + ASSUMED_MMR))))
        lev = max(lev, 1)
        margin = notional / lev
        if margin > acct.available * MAX_MARGIN_SHARE:
            # shrink to fit margin rather than raising leverage
            qty = inst.round_qty_down(acct.available * MAX_MARGIN_SHARE * lev / entry)
            notional = qty * entry
            margin = notional / lev
            if qty < inst.min_qty or notional < inst.min_notional:
                return RiskDecision(False, ["insufficient_available_margin"], None, checks)
        liq = entry * (1 - side * (1.0 / lev - ASSUMED_MMR))
        if (stop - liq) * side <= 0:
            return RiskDecision(False, ["liquidation_before_stop"], None, checks)

        # --- split for partial profit taking (TP1 must itself be a valid order size)
        tp1_qty = inst.round_qty_down(qty * TP1_FRACTION)
        tp2_qty = inst.round_qty_down(qty - tp1_qty)
        if tp1_qty < inst.min_qty or tp2_qty < inst.min_qty:
            tp1_qty, tp2_qty = 0.0, qty        # too small to split: single target at TP2, still stop-protected
        r_unit = abs(entry - stop)
        tp1 = inst.round_price(entry + side * TP1_R * r_unit)
        tp2 = inst.round_price(entry + side * TP2_R * r_unit)

        planned_risk = qty * loss_per_unit
        fees_win = qty * entry_fill * inst.taker_fee + tp1_qty * tp1 * inst.maker_fee + tp2_qty * tp2 * inst.maker_fee
        reward = (tp1_qty * (tp1 - entry_fill) + tp2_qty * (tp2 - entry_fill)) * side - fees_win
        est_fees = qty * (entry_fill + stop_fill) * inst.taker_fee
        plan = TradePlan(
            symbol=inst.symbol, strategy=strategy, interval=interval, side=side, signal_ts=signal_ts,
            entry=inst.round_price(entry), stop=inst.round_price(stop), tp1=tp1, tp2=tp2,
            qty=qty, tp1_qty=tp1_qty, tp2_qty=tp2_qty, notional=notional,
            planned_risk=planned_risk, risk_pct=planned_risk / acct.equity,
            effective_leverage=notional / acct.equity,
            portfolio_leverage_after=(acct.gross_exposure + notional) / acct.equity,
            exchange_leverage=lev, margin_mode="isolated", margin_required=margin, est_liq_price=liq,
            est_fees=est_fees, expected_net_rr=reward / planned_risk if planned_risk > 0 else 0.0)

        # --- final invariants (defence in depth; these must hold for every approved plan)
        eps = 1e-9
        if plan.risk_pct > L.risk_per_trade + eps:
            reasons.append("invariant_risk_per_trade")
        if acct.open_planned_risk + plan.planned_risk > L.max_open_risk * acct.equity + eps:
            reasons.append("invariant_open_risk")
        if plan.portfolio_leverage_after > L.max_gross_exposure + eps:
            reasons.append("invariant_gross_exposure")
        if plan.expected_net_rr < 1.0:
            reasons.append(f"net_rr_below_1: {plan.expected_net_rr:.2f}")
        checks.update({"loss_per_unit": loss_per_unit, "stop_pct": stop_pct})
        return RiskDecision(not reasons, reasons, plan if not reasons else None, checks)
