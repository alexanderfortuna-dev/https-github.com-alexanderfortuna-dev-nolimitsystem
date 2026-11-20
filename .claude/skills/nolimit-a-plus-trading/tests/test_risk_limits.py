import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import _common
import calculate_position_size as cps
import evaluate_portfolio_risk as epr

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
DOC = json.load(open(os.path.join(EX, "valid_trade.json")))
ACC, REQ = DOC["account"], DOC["sizing_request"]
BAD = json.load(open(os.path.join(EX, "rejected_trade.json")))["account"]


def acc(**ch):
    a = copy.deepcopy(ACC)
    a.update(ch)
    return a


def test_limits_can_only_be_tightened():
    assert _common.tightened({"risk_per_trade": 0.05, "max_gross_exposure": 10})["risk_per_trade"] == 0.005
    assert _common.tightened({"max_gross_exposure": 10})["max_gross_exposure"] == 3.0
    assert _common.tightened({"risk_per_trade": 0.0025})["risk_per_trade"] == 0.0025
    assert _common.LIVE_EXECUTION_ENABLED is False
    r = cps.size(dict(REQ, limits={"risk_per_trade": 0.05}))
    assert r["plan"]["risk_pct_of_equity"] <= 0.005


def test_clean_account_may_open_and_ledgers_are_counted_once():
    r = epr.evaluate(ACC)
    assert r["can_open_new_position"] and r["total_equity_all_ledgers"] == 12500.0 and r["overview_cross_check"] == 0.0
    assert r["existing_positions_modified"] is False


def test_double_counting_is_caught_by_the_cross_check():
    a = acc(ledgers={"unified": 10000, "funding": 500, "bots": 2000, "cfd": 0, "spot_again": 6000})
    r = epr.evaluate(a)
    assert not r["can_open_new_position"] and any("equity_cross_check_failed" in x for x in r["reasons"])


def test_circuit_breakers():
    assert any("daily_loss" in x for x in epr.evaluate(acc(trading_equity=9850))["reasons"])
    just_inside = acc(trading_equity=9851, ledgers={"unified": 9851, "funding": 500, "bots": 2000, "cfd": 0}, reported_overview_total=12351,
                      candidate=dict(ACC["candidate"], planned_risk=49.0))
    assert epr.evaluate(just_inside)["can_open_new_position"]          # -1.49% on the day: still allowed
    assert any("weekly_loss" in x for x in epr.evaluate(acc(week_start_equity=10500))["reasons"])


def test_candidate_risk_above_per_trade_limit_is_blocked():
    r = epr.evaluate(acc(trading_equity=9851, ledgers={"unified": 9851, "funding": 500, "bots": 2000, "cfd": 0}, reported_overview_total=12351))
    assert any("candidate_risk_exceeds_per_trade_limit" in x for x in r["reasons"])   # 49.94 > 0.5% of 9,851


def test_open_risk_and_exposure_caps_include_existing_and_pending():
    pos = [{"symbol": f"S{i}", "side": "long", "notional": 1000, "asset_group": f"g{i}", "managed": True, "planned_risk": 50} for i in range(3)]
    r = epr.evaluate(acc(positions=pos))
    assert any("open_risk_cap" in x for x in r["reasons"])           # 150 + 49.94 > 150
    pos = [{"symbol": "ETHUSDT", "side": "long", "notional": 26000, "asset_group": "x", "managed": False}]
    assert any("gross_exposure_cap" in x for x in epr.evaluate(acc(positions=pos))["reasons"])
    r = epr.evaluate(acc(pending_orders=[{"symbol": "SOLUSDT", "side": "buy", "notional": 27000, "reduce_only": False}]))
    assert any("gross_exposure_cap" in x for x in r["reasons"])
    r = epr.evaluate(acc(pending_orders=[{"symbol": "SOLUSDT", "side": "sell", "notional": 27000, "reduce_only": True}]))
    assert r["can_open_new_position"]                                 # reduce-only orders add no exposure


def test_no_adding_to_an_existing_symbol_and_concentration():
    pos = [{"symbol": "BTCUSDT", "side": "long", "notional": 500, "asset_group": "crypto", "managed": False}]
    assert any("existing_position_on_symbol" in x for x in epr.evaluate(acc(positions=pos))["reasons"])
    pos = [{"symbol": s, "side": "long", "notional": 500, "asset_group": "crypto", "managed": True, "planned_risk": 30} for s in ("ETHUSDT", "SOLUSDT")]
    assert any("concentration" in x for x in epr.evaluate(acc(positions=pos))["reasons"])


def test_stale_incomplete_or_killed_account_fails_closed():
    assert any("stale" in x for x in epr.evaluate(acc(now_ms=ACC["now_ms"] + 600_000))["reasons"])
    assert any("account_incomplete" in x for x in epr.evaluate(acc(complete=False))["reasons"])
    assert any("kill_switch" in x for x in epr.evaluate(acc(kill_switch=True))["reasons"])
    a = acc(); del a["snapshot_ts_ms"]
    assert not epr.evaluate(a)["can_open_new_position"]
    assert not epr.evaluate(acc(trading_equity=None))["can_open_new_position"]


def test_rejected_example_account():
    r = epr.evaluate(BAD)
    assert not r["can_open_new_position"] and any("gross_exposure_cap" in x for x in r["reasons"])
    assert any("cfd" in w for w in r["warnings"]) and any("BTCUSDT" in w for w in r["warnings"])
    assert r["effective_leverage"] > 2.9
