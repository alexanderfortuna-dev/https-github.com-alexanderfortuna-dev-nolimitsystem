import time

import pytest

from aplus.risk import PositionView, RiskEngine, KillSwitch
from conftest import fresh_account, make_inst

OK = "OOS_PASSED_FORWARD_PENDING"


def ev(risk, inst, acct, entry=60_000.0, stop=59_400.0, side=1, status=OK, ticker_age=1.0, slippage=0.0002):
    return risk.evaluate(inst=inst, strategy="TPB", interval="4H", side=side, signal_ts=1, entry=entry, stop=stop,
                         acct=acct, slippage=slippage, ticker_age_s=ticker_age, validation_status=status)


@pytest.fixture
def risk(limits, tmp_path):
    return RiskEngine(limits, KillSwitch(tmp_path / "KILL"))


def test_size_comes_from_stop_distance_and_never_exceeds_half_percent(risk, inst):
    d = ev(risk, inst, fresh_account(10_000))
    p = d.plan
    assert d.approved
    assert p.planned_risk <= 50.0 + 1e-9 and p.planned_risk > 48.0         # 0.5% of 10k, rounded down to lot size
    # independent recomputation of the loss at the stop incl. fees and slippage
    ef, sf = 60_000 * 1.0002, 59_400 * (1 - 0.0003)
    loss = p.qty * ((ef - sf) + (ef + sf) * 0.0006)
    assert loss == pytest.approx(p.planned_risk, rel=1e-9)
    assert p.risk_pct <= 0.005


def test_effective_leverage_is_not_the_exchange_setting(risk, inst):
    p = ev(risk, inst, fresh_account(10_000)).plan
    assert p.effective_leverage == pytest.approx(p.notional / 10_000)
    assert p.effective_leverage < 1.0            # 1% stop, 0.5% risk -> about 0.44x of equity
    assert p.exchange_leverage >= 10             # the SETTING is much higher, and that is fine
    assert p.margin_required == pytest.approx(p.notional / p.exchange_leverage)
    assert p.margin_mode == "isolated"
    # liquidation must sit at least 2 stop-distances away
    assert (60_000 - p.est_liq_price) >= 2 * (60_000 - 59_400)


def test_short_plan_is_mirrored(risk, inst):
    p = ev(risk, inst, fresh_account(), entry=60_000, stop=60_600, side=-1).plan
    assert p.tp1 < 60_000 and p.tp2 < p.tp1 and p.est_liq_price > 60_600 and p.expected_net_rr > 1.5


@pytest.mark.parametrize("status", ["REJECTED", "", "UNKNOWN"])
def test_no_validated_edge_no_trade(risk, inst, status):
    d = ev(risk, inst, fresh_account(), status=status)
    assert not d.approved and d.plan is None and "edge_not_validated" in d.reasons[0]


def test_incomplete_or_stale_account_blocks_entries(risk, inst):
    assert "account_incomplete" in ev(risk, inst, fresh_account(complete=False, issues=["positions: timeout"])).reasons[0]
    assert "account_snapshot_stale" in ev(risk, inst, fresh_account(ts=time.time() - 600)).reasons[0]


def test_stale_ticker_blocks(risk, inst):
    assert "ticker_stale" in ev(risk, inst, fresh_account(), ticker_age=60).reasons[0]


def test_kill_switch_blocks(risk, inst):
    risk.kill.engage("test")
    assert "kill_switch_active" in ev(risk, inst, fresh_account()).reasons[0]


def test_daily_and_weekly_circuit_breakers(risk, inst):
    assert any("daily_loss_breaker" in r for r in ev(risk, inst, fresh_account(9_850, day_start_equity=10_000)).reasons)
    assert ev(risk, inst, fresh_account(9_851, day_start_equity=10_000)).approved
    assert any("weekly_loss_breaker" in r for r in ev(risk, inst, fresh_account(9_600, week_start_equity=10_000)).reasons)


def test_open_risk_cap_allows_three_trades_not_four(risk, inst):
    assert ev(risk, inst, fresh_account(open_planned_risk=100.0)).approved        # 1.0% used
    d = ev(risk, inst, fresh_account(open_planned_risk=101.0))                    # cannot fit another 0.5%
    assert not d.approved and "open_risk_cap" in d.reasons[0]


def test_gross_exposure_cap_counts_existing_user_positions(risk, inst):
    held = [PositionView("ETHUSDT", 1, 10, 3_000.0, managed=False)]               # 30k = 3.0x of 10k
    d = ev(risk, inst, fresh_account(positions=held))
    assert not d.approved and "gross_exposure_cap" in d.reasons[0]
    held = [PositionView("ETHUSDT", 1, 9.9, 3_000.0, managed=False)]              # 2.97x: only 300 USDT of room
    p = ev(risk, inst, fresh_account(positions=held)).plan
    assert p.notional <= 300.0 and p.portfolio_leverage_after <= 3.0 and p.risk_pct < 0.005


def test_never_adds_to_an_existing_position_on_the_symbol(risk, inst):
    held = [PositionView("BTCUSDT", 1, 0.01, 59_000.0, managed=False)]
    assert "existing_position_on_symbol" in ev(risk, inst, fresh_account(positions=held)).reasons


def test_stop_on_wrong_side_and_bad_prices(risk, inst):
    assert "stop_on_wrong_side" in ev(risk, inst, fresh_account(), entry=60_000, stop=60_500, side=1).reasons
    assert "invalid_prices" in ev(risk, inst, fresh_account(), entry=float("nan")).reasons


def test_below_minimum_order_is_rejected_not_rounded_up(risk):
    big = make_inst(step=1.0, min_qty=1.0)
    d = ev(risk, big, fresh_account(1_000))
    assert not d.approved and "below_minimum_order" in d.reasons[0]


def test_margin_shortage_shrinks_size_instead_of_raising_leverage(risk, inst):
    full = ev(risk, inst, fresh_account(10_000)).plan
    p = ev(risk, inst, fresh_account(10_000, available=100.0)).plan
    assert p.exchange_leverage == full.exchange_leverage and p.qty < full.qty and p.margin_required <= 50.0 + 1e-6


def test_wide_stop_gets_low_leverage_setting(risk, inst):
    p = ev(risk, inst, fresh_account(), entry=60_000, stop=54_000).plan           # 10% stop
    assert p.exchange_leverage <= 4 and (60_000 - p.est_liq_price) >= 2 * 6_000
