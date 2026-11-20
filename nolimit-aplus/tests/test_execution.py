"""Order lifecycle and failure simulations on the paper broker (deterministic prices)."""
import time

import pytest

from aplus.execution.broker import client_oid


def orders(r, key):
    return {o["role"]: o for o in r.store.select("orders", {"trade_key": key})}


def events(r, kind):
    return [e for e in r.store.select("events") if e["kind"] == kind]


def enter(r, **kw):
    p, key = r.arm(**kw)
    r.market.px["BTCUSDT"] = p.entry + (10 if p.side == 1 else -10)
    r.engine.tick()
    assert r.trade(key)["state"] == "OPEN"
    return p, key


def test_trigger_not_reached_sends_nothing(rig):
    p, key = rig.arm()
    rig.engine.tick()
    assert rig.trade(key)["state"] == "ARMED" and not rig.broker.orders and orders(rig, key) == {}


def test_happy_path_tp1_breakeven_tp2(rig):
    p, key = enter(rig)
    o = orders(rig, key)
    assert set(o) == {"entry", "tp1", "tp2"}
    assert o["tp1"]["reduce_only"] and o["tp2"]["reduce_only"] and not o["entry"]["reduce_only"]
    assert rig.broker.protective_stop("BTCUSDT") == p.stop            # stop exists from the first moment
    rig.market.px["BTCUSDT"] = p.tp1 + 5
    rig.engine.tick(); rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "TP1_DONE" and t["qty_open"] == pytest.approx(p.tp2_qty)
    assert rig.broker.protective_stop("BTCUSDT") == pytest.approx(t["entry_fill"])   # moved to entry
    rig.market.px["BTCUSDT"] = p.tp2 + 5
    rig.engine.tick(); rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "CLOSED" and t["exit_reason"] == "tp2"
    assert t["net_r"] == pytest.approx(p.expected_net_rr, abs=0.08) and t["net_r"] > 1.5
    assert not rig.broker.pos and rig.engine.account_state().open_planned_risk == 0


def test_stop_loss_path_loses_about_one_r(rig):
    p, key = enter(rig)
    rig.market.px["BTCUSDT"] = p.stop - 50
    rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "CLOSED" and t["exit_reason"] == "stop"
    assert -1.05 < t["net_r"] <= -0.95
    assert all(o.status != "live" for o in rig.broker.orders.values())   # TP orders cancelled, nothing left working


def test_breakeven_stop_after_tp1(rig):
    p, key = enter(rig)
    rig.market.px["BTCUSDT"] = p.tp1 + 5
    rig.engine.tick(); rig.engine.tick()
    rig.market.px["BTCUSDT"] = p.entry - 100
    rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "CLOSED" and t["exit_reason"] == "breakeven_stop" and 0.3 < t["net_r"] < 0.5


def test_duplicate_signal_cannot_create_a_second_trade(rig):
    p, key = rig.arm()
    assert rig.engine.submit(p, "BTCUSDT|TPB|4H|1", int(time.time() * 1000) + 60_000) == "DUPLICATE"
    assert len(rig.store.select("trades")) == 1 and events(rig, "duplicate_trade_blocked")


def test_timeout_after_acceptance_is_reconciled_not_resent(rig):
    rig.broker.fail_next["entry"] = "timeout"            # exchange accepted, response lost
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    assert rig.trade(key)["state"] == "ENTRY_SUBMITTING" and events(rig, "entry_submit_error")
    for _ in range(3):
        rig.engine.tick()
    assert rig.trade(key)["state"] == "OPEN"
    assert rig.broker.pos["BTCUSDT"].qty == pytest.approx(p.qty)           # exactly one position, not two
    assert sum(1 for f in rig.broker._fills if not f.closing) == 1


def test_duplicate_order_row_blocks_resubmission(rig):
    p, key = rig.arm()
    rig.store.insert("orders", {"client_oid": client_oid(key, "entry"), "trade_key": key, "role": "entry",
                                "status": "SUBMITTING", "side": "buy", "qty": p.qty, "reduce_only": False,
                                "created_at": "2020-01-01T00:00:00+00:00", "updated_at": "2020-01-01T00:00:00+00:00"})
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    assert not rig.broker.orders and events(rig, "duplicate_order_blocked")   # nothing was sent
    rig.engine.tick()                                                          # unknown to exchange + past grace
    assert rig.trade(key)["state"] == "REJECTED"


def test_entry_rejected_by_exchange(rig):
    rig.broker.fail_next["entry"] = "reject"
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    assert rig.trade(key)["state"] == "REJECTED" and not rig.broker.pos
    assert rig.engine.account_state().open_planned_risk == 0


def test_no_protective_stop_means_immediate_flatten(rig):
    rig.broker.fail_next.update(entry="no_stop", move_stop="reject")   # stop missing AND cannot be restored
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick(); rig.engine.tick()
    t = rig.trade(key)
    assert not rig.broker.pos and t["state"] == "CLOSED" and t["exit_reason"] == "no_protective_stop"
    assert events(rig, "no_protective_stop")


def test_missing_stop_is_restored_when_possible(rig):
    rig.broker.fail_next["entry"] = "no_stop"
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    assert rig.broker.protective_stop("BTCUSDT") == p.stop and events(rig, "stop_restored")


def test_tp_rejection_leaves_position_protected(rig):
    rig.broker.fail_next["reduce_limit"] = "reject"
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    assert rig.trade(key)["state"] == "OPEN" and events(rig, "tp_rejected")
    assert rig.broker.protective_stop("BTCUSDT") == p.stop


def test_stale_quote_is_rejected(rig):
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.market.age = 120.0
    rig.engine.tick()
    assert rig.trade(key)["state"] == "ARMED" and not rig.broker.orders and events(rig, "stale_data_rejected")


def test_never_chases_price_far_beyond_trigger(rig):
    p, key = rig.arm()
    rig.market.px["BTCUSDT"] = p.entry + 0.5 * (p.entry - p.stop)
    rig.engine.tick()
    assert rig.trade(key)["state"] == "ARMED" and not rig.broker.orders


def test_expired_trigger_is_cancelled(rig):
    p = rig.plan()
    rig.engine.submit(p, "BTCUSDT|TPB|4H|1", int(time.time() * 1000) - 1)
    rig.engine.tick()
    assert rig.trade("paper|BTCUSDT|TPB|4H|1")["state"] == "EXPIRED"


def test_kill_switch_cancels_armed_and_blocks_new(rig):
    p, key = rig.arm()
    rig.engine.emergency_stop("test")
    assert rig.trade(key)["state"] == "CANCELLED"
    assert rig.engine.submit(p, "BTCUSDT|TPB|4H|99", int(time.time() * 1000) + 60_000) == "BLOCKED"


def test_kill_switch_with_flatten_closes_managed_position_reduce_only(rig):
    p, key = enter(rig)
    rig.engine.emergency_stop("test", flatten=True)
    rig.engine.tick()
    t = rig.trade(key)
    assert not rig.broker.pos and t["state"] == "CLOSED" and t["exit_reason"] == "kill_switch"
    assert orders(rig, key)["flatten"]["reduce_only"]


def test_kill_switch_without_flatten_keeps_protected_position(rig):
    p, key = enter(rig)
    rig.engine.emergency_stop("test")
    assert rig.trade(key)["state"] == "OPEN" and rig.broker.protective_stop("BTCUSDT") == p.stop


def test_position_mismatch_engages_kill_switch(rig):
    p, key = enter(rig)
    rig.broker.pos["BTCUSDT"].qty *= 2          # exchange shows a size the book cannot explain
    rig.engine.tick()
    assert rig.kill.active and "position mismatch" in rig.kill.reason()


def test_external_positions_are_reported_and_never_touched(rig):
    from aplus.execution.broker import _PaperPos
    from conftest import make_inst
    rig.broker._insts["ETHUSDT"] = make_inst("ETHUSDT")
    rig.market.px["ETHUSDT"] = 3_000.0
    rig.broker.pos["ETHUSDT"] = _PaperPos(1, 1.0, 3_000.0, None)     # the owner's own position, no stop
    findings = rig.engine.reconcile()
    assert any("EXTERNAL position ETHUSDT" in f for f in findings)
    rig.engine.emergency_stop("test", flatten=True)
    rig.engine.tick()
    assert rig.broker.pos["ETHUSDT"].qty == 1.0 and rig.broker.pos["ETHUSDT"].stop is None
    assert not any(o.symbol == "ETHUSDT" for o in rig.broker.orders.values())


def test_fills_are_booked_exactly_once(rig):
    p, key = enter(rig)
    for _ in range(5):
        rig.engine.tick()
    assert len(rig.store.select("fills", {"trade_key": key})) == 1


def test_time_exit(rig, monkeypatch):
    p, key = enter(rig)
    rig.engine.clock = lambda: time.time() + 61 * 4 * 3600
    rig.market.age = -61 * 4 * 3600 - 0.0        # keep quotes "fresh" relative to the shifted clock
    rig.engine.tick(); rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "CLOSED" and t["exit_reason"] == "time_exit" and not rig.broker.pos


def test_circuit_breaker_cancels_armed_entry_at_trigger_time(rig):
    p, key = rig.arm()
    rig.broker.cash -= 200.0                     # -2% on the day after the plan was armed
    rig.market.px["BTCUSDT"] = p.entry + 10
    rig.engine.tick()
    t = rig.trade(key)
    assert t["state"] == "CANCELLED" and "daily_loss_breaker" in t["exit_reason"] and not rig.broker.pos


def test_engine_refuses_unknown_broker_mode(rig):
    from aplus.execution.engine import ExecutionEngine
    rig.broker.mode = "live"
    with pytest.raises(ValueError, match="production trading is disabled"):
        ExecutionEngine(rig.broker, rig.store, rig.risk, rig.kill, {}, rig.risk.limits)
