import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import calculate_position_size as cps

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
DOC = json.load(open(os.path.join(EX, "valid_trade.json")))
REQ = DOC["sizing_request"]


def req(**ch):
    r = copy.deepcopy(REQ)
    r.update(ch)
    return r


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_manual_example_matches_to_the_cent():
    # hand calculation: loss/unit = (60012 - 59382.18) + (60012 + 59382.18) * 0.0006 = 701.4565308
    r = cps.size(REQ)
    p = r["plan"]
    assert r["approved"]
    assert p["position_size"] == 0.0712                       # 50 / 701.4565 = 0.07128 -> rounded DOWN
    assert p["notional"] == 4272.0 and p["effective_leverage"] == 0.4272
    assert p["leverage_setting"] == 20 and p["margin_required"] == 213.6 and p["margin_mode"] == "isolated"
    assert p["maximum_planned_loss"] == 49.94 and p["risk_pct_of_equity"] <= 0.005
    assert p["take_profit_1"] == 60600.0 and p["take_profit_2"] == 61800.0
    assert p["estimated_liquidation_price"] == 58200.0        # 60000 * (1 - (1/20 - 0.02))
    for k, v in DOC["expected_plan"].items():
        assert p[k] == v, k


def test_leverage_request_never_increases_size_or_risk():
    base = cps.size(REQ)["plan"]
    hi = cps.size(req(requested_leverage=100))["plan"]
    assert hi["position_size"] == base["position_size"] and hi["maximum_planned_loss"] == base["maximum_planned_loss"]
    assert hi["leverage_setting"] == 20 and any("ignored" in n for n in hi["notes"])
    lo = cps.size(req(requested_leverage=3))["plan"]
    assert lo["leverage_setting"] == 3 and lo["position_size"] == base["position_size"]
    assert close(lo["margin_required"], 1424.0)


def test_short_is_mirrored_and_liquidation_is_beyond_stop():
    p = cps.size(req(side="short", entry=60000, stop=60600))["plan"]
    assert p["take_profit_1"] == 59400.0 and p["take_profit_2"] == 58200.0
    assert p["estimated_liquidation_price"] >= 60000 + 2 * 600


def test_wide_stop_gets_small_size_and_low_leverage():
    p = cps.size(req(entry=60000, stop=54000))["plan"]        # 10% stop
    assert p["leverage_setting"] <= 4 and p["effective_leverage"] < 0.06
    assert 60000 - p["estimated_liquidation_price"] >= 2 * 6000


def test_slippage_and_funding_reduce_size():
    base = cps.size(REQ)["plan"]["position_size"]
    assert cps.size(req(slippage=0.002))["plan"]["position_size"] < base
    f = cps.size(req(funding_rate_per_interval=0.0001, funding_interval_h=8, expected_hold_h=48))["plan"]
    assert f["position_size"] < base and f["estimated_funding_cost"] > 0
    # funding heavy enough to destroy the reward/risk rejects the trade outright
    heavy = cps.size(req(funding_rate_per_interval=0.0005, funding_interval_h=8, expected_hold_h=240))
    assert not heavy["approved"] and heavy["reasons"][0].startswith("net_reward_risk_below_1")
    # funding that PAYS the position is never counted as income
    s = cps.size(req(side="short", entry=60000, stop=60600, funding_rate_per_interval=0.0005, expected_hold_h=240))["plan"]
    assert s["estimated_funding_cost"] == 0


def test_currency_conversion_scales_quantity():
    p = cps.size(req(quote_to_account_rate=2.0))["plan"]      # quote currency worth 2 account units
    assert p["position_size"] == 0.0356 and p["maximum_planned_loss"] <= 50.0


def test_margin_shortage_shrinks_size_not_raises_leverage():
    p = cps.size(req(available=100))["plan"]
    assert p["leverage_setting"] == 20 and p["margin_required"] <= 50.0 and p["position_size"] < 0.0712


def test_rejections():
    assert "stop_on_wrong_side" in cps.size(req(stop=60500))["reasons"]
    assert cps.size(req(entry=None))["reasons"][0].startswith("missing_input")
    assert cps.size(req(equity=0))["reasons"] == ["equity_not_positive"]
    big = dict(REQ["contract"], qty_step=1.0, min_qty=1.0)
    assert cps.size(req(contract=big, equity=1000, available=1000))["reasons"][0].startswith("below_minimum_order")
    assert cps.size(req(side="sideways"))["reasons"][0].startswith("missing_input")
    c = dict(REQ["contract"]); del c["taker_fee"]
    assert "contract.taker_fee" in cps.size(req(contract=c))["reasons"][0]
    assert cps.size(req(entry=1.0, stop=1.4, side="short"))["reasons"][0].startswith("target_not_a_valid_price")
