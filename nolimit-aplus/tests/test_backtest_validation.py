import numpy as np
import pandas as pd
import pytest

from aplus.backtest import CostModel, simulate, stats
from aplus.data import INTERVAL_MS, assert_fresh, drop_unclosed, funding_per_bar, quality_report
from aplus.bitget.client import StaleDataError
from aplus.indicators import add_indicators
from aplus.strategies import Setup, generate_setups
from aplus import validation as V

H4 = INTERVAL_MS["4H"]
FREE = CostModel(taker_fee=0, maker_fee=0, slippage=0, price_tick=0)


def bars(rows):
    """rows: (open, high, low, close)"""
    return pd.DataFrame([{"ts": i * H4, "open": o, "high": h, "low": l, "close": c, "volume": 1.0, "turnover": 1.0}
                         for i, (o, h, l, c) in enumerate(rows)])


LONG = Setup("T", 0, 0, 1, 100.0, 98.0, 2)        # trigger 100, stop 98 -> R = 2, tp1 102, tp2 106


def run(rows, cost=FREE, setup=LONG, **kw):
    return simulate("X", "4H", bars(rows), [setup], cost, **kw)


def test_full_winner_is_two_r():
    t = run([(99, 99.5, 98.5, 99), (99, 101, 99, 100.5), (100.5, 103, 100.2, 102.5), (102.5, 107, 102, 106.5), (106, 107, 105, 106)])
    assert len(t) == 1 and t[0].exit_reason == "tp2" and t[0].net_r == pytest.approx(0.5 * 1 + 0.5 * 3)


def test_stop_and_target_in_same_bar_assumes_stop_first():
    t = run([(99, 99.5, 98.5, 99), (99, 101, 99, 100.5), (100.5, 107, 97, 105), (105, 106, 104, 105)])
    assert t[0].exit_reason == "stop" and t[0].net_r == pytest.approx(-1.0)


def test_entry_bar_touching_stop_is_a_loss():
    t = run([(99, 99.5, 98.5, 99), (99, 103, 97.5, 102), (102, 107, 101, 106), (106, 107, 105, 106)])
    assert t[0].exit_reason == "stop" and t[0].net_r == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_the_worse_open():
    t = run([(99, 99.5, 98.5, 99), (99, 101, 99, 100.5), (95, 96, 94, 95), (95, 96, 94, 95)])
    assert t[0].net_r == pytest.approx((95 - 100) / 2)        # -2.5R, not -1R


def test_gap_up_entry_fills_at_open_not_trigger():
    t = run([(99, 99.5, 98.5, 99), (101, 101.5, 100.5, 101), (101, 101.2, 97, 97.5), (97, 98, 96, 97)])
    assert t[0].entry_fill == 101 and t[0].net_r == pytest.approx((98 - 101) / 2)


def test_target_needs_trade_through_not_touch():
    rows = [(99, 99.5, 98.5, 99), (99, 101, 99, 100.5), (100.5, 102.0, 100.2, 101.5), (101.5, 101.8, 97, 97.5), (97, 98, 96, 97)]
    t = run(rows, CostModel(0, 0, 0, price_tick=0.1))
    assert t[0].exit_reason == "stop"                          # high == tp1 exactly: limit not assumed filled


def test_trigger_expires_and_no_trade_without_fill():
    assert run([(99, 99.5, 98.5, 99), (99, 99.8, 98.5, 99), (99, 99.9, 98.5, 99), (99, 105, 98.5, 104), (104, 105, 103, 104)]) == []


def test_costs_always_reduce_results():
    rows = [(99, 99.5, 98.5, 99), (99, 101, 99, 100.5), (100.5, 103, 100.2, 102.5), (102.5, 107, 102, 106.5), (106, 107, 105, 106)]
    free, paid = run(rows)[0], run(rows, CostModel(0.0006, 0.0002, 0.0004))[0]
    assert paid.net_r < free.net_r and paid.fees_r > 0
    assert run(rows, CostModel(0.0006, 0.0002, 0.0004).stressed())[0].net_r < paid.net_r


def test_funding_is_charged_and_unknown_history_is_pessimistic():
    rows = [(99, 99.5, 98.5, 99), (99, 101, 99, 100.5)] + [(100.5, 101, 100.2, 100.6)] * 5 + [(100.6, 107, 100.5, 106.5), (106, 107, 105, 106)]
    n = len(rows)
    base = run(rows)[0].net_r
    assert run(rows, funding_signed=np.full(n, 0.001))[0].net_r < base             # long pays positive funding
    assert run(rows, funding_signed=np.full(n, -0.001))[0].net_r > base
    assert run(rows, funding_unknown=np.full(n, 0.001))[0].net_r < base            # unknown: always a cost
    short = Setup("T", 0, 0, -1, 98.5, 100.5, 2)
    rows_s = [(99, 99.5, 98.6, 99), (99, 99.2, 98, 98.2)] + [(98.2, 98.4, 98.0, 98.2)] * 3 + [(98.2, 98.3, 90, 91), (91, 92, 90, 91)]
    b = run(rows_s, setup=short)[0].net_r
    assert run(rows_s, setup=short, funding_unknown=np.full(len(rows_s), 0.001))[0].net_r < b


def test_one_position_at_a_time_no_pyramiding():
    rows = [(99, 99.5, 98.5, 99)] * 2 + [(99, 101, 99, 100.5)] + [(100.5, 101, 100.2, 100.6)] * 4 + [(100.6, 107, 100.5, 106.5), (106, 107, 105, 106)]
    setups = [Setup("T", i, i * H4, 1, 100.0, 98.0, 2) for i in range(0, 6)]
    assert len(simulate("X", "4H", bars(rows), setups, FREE)) == 1


def random_walk(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, n)))
    return pd.DataFrame({"ts": np.arange(n) * H4, "open": o, "high": h, "low": l, "close": c, "volume": 1.0, "turnover": 1.0})


@pytest.mark.parametrize("strategy", ["TPB", "DBO"])
def test_no_lookahead_signals_do_not_change_when_future_is_altered(strategy):
    df = random_walk()
    cut = 1000
    _, full = generate_setups(df, strategy)
    altered = df.copy()
    altered.loc[cut:, ["open", "high", "low", "close"]] *= 3.0
    _, alt = generate_setups(altered, strategy)
    assert [s for s in full if s.bar_index < cut] == [s for s in alt if s.bar_index < cut]
    assert len([s for s in full if s.bar_index < cut]) > 5
    for s in full:                                    # stop is always on the losing side of the trigger
        assert (s.trigger - s.stop) * s.side > 0


def test_indicators_are_causal():
    df = random_walk(600)
    a = add_indicators(df).iloc[:400]
    b = add_indicators(df.iloc[:400])
    pd.testing.assert_frame_equal(a, b)


def test_data_guards():
    df = bars([(1.0, 2.0, 0.5, 1.5)] * 10)
    assert len(drop_unclosed(df, "4H", 9 * H4 + 1)) == 9          # the forming bar is never used
    with pytest.raises(StaleDataError):
        assert_fresh(df, "4H", 20 * H4, 1.05)
    assert_fresh(df, "4H", 10 * H4 + 1000, 1.05)
    assert quality_report(df, "4H")["ok"]
    broken = df.copy(); broken.loc[3, "high"] = 0.1
    assert not quality_report(broken, "4H")["ok"]
    gappy = df.drop(index=[4, 5, 6]).reset_index(drop=True)
    assert not quality_report(gappy, "4H")["ok"]


def test_funding_alignment():
    df = bars([(1, 2, 0.5, 1.5)] * 6)
    f = pd.DataFrame({"ts": [3 * H4 + 5, 5 * H4 + 5], "rate": [0.001, -0.002]})
    signed, unknown = funding_per_bar(df, f, "4H", 8.0)
    assert signed.tolist() == [0, 0, 0, 0.001, 0, -0.002]
    assert (unknown[:3] > 0).all() and (unknown[4:] == 0).all()


# ----------------------------------------------------------------------------- validation framework
def make_trades(mean, n=600, seed=1, symbols=6):
    rng = np.random.default_rng(seed)
    week = 7 * 86_400_000
    ts = np.sort(rng.integers(0, 150 * week, n))
    return pd.DataFrame({"symbol": [f"S{i % symbols}" for i in range(n)], "strategy": "TPB", "interval": "4H", "group": "g",
                         "signal_ts": ts, "entry_ts": ts, "net_r": rng.normal(mean, 1.3, n),
                         "first_bar_ts": 0, "last_bar_ts": 150 * week})


def test_real_edge_passes_and_noise_is_rejected():
    good = make_trades(0.35)
    res = V.validate_pools(good, good.assign(net_r=good.net_r - 0.08))
    assert res[0].status == V.OOS_PASSED and res[0].failed_gates == []
    noise = make_trades(0.0, seed=5)
    res = V.validate_pools(noise, noise.assign(net_r=noise.net_r - 0.08))
    assert res[0].status == V.REJECTED and "ci_lower" in res[0].failed_gates


def test_edge_that_dies_under_cost_stress_is_rejected():
    t = make_trades(0.35)
    res = V.validate_pools(t, t.assign(net_r=t.net_r - 0.6))
    assert res[0].status == V.REJECTED and "cost_stress" in res[0].failed_gates


def test_split_is_chronological_60_40():
    t = V.split_is_oos(make_trades(0.1))
    assert t[t["sample"] == "IS"]["signal_ts"].max() < t[t["sample"] == "OOS"]["signal_ts"].min()
    assert 0.3 < (t["sample"] == "OOS").mean() < 0.5


def test_holm_is_more_conservative_with_more_tests():
    assert V.holm([0.01]) == [0.01]
    adj = V.holm([0.01, 0.04, 0.03, 0.5])
    assert adj[0] == pytest.approx(0.04) and all(a >= p for a, p in zip(adj, [0.01, 0.04, 0.03, 0.5])) and max(adj) <= 1


def test_symbol_cannot_pass_inside_a_rejected_pool():
    noise = make_trades(0.0, seed=5)
    pools = V.validate_pools(noise, noise)
    syms = V.validate_symbols(noise, noise, pools)
    assert (syms["status"] == V.REJECTED).all() and syms["reason"].str.contains("pool_rejected").all()


def test_forward_gate():
    assert V.apply_forward_gate(V.OOS_PASSED, [0.5] * 19) == V.OOS_PASSED
    assert V.apply_forward_gate(V.OOS_PASSED, [0.5] * 20) == V.VALIDATED
    assert V.apply_forward_gate(V.OOS_PASSED, [-0.5] * 30) == V.OOS_PASSED
    assert V.apply_forward_gate(V.REJECTED, [5.0] * 100) == V.REJECTED


def test_stats_drawdown():
    s = stats(np.array([1, -1, -1, -1, 2.0]))
    assert s["max_dd_r"] == 3 and s["trades"] == 5 and s["total_r"] == 0


@pytest.mark.parametrize("strategy", ["TPB", "DBO"])
def test_setups_always_have_reachable_targets(strategy):
    df = random_walk(1500, seed=9)
    df[["open", "high", "low", "close"]] *= np.linspace(1, 0.02, len(df))[:, None]     # violent collapse: huge ATR vs price
    _, setups = generate_setups(df, strategy)
    for s in setups:
        assert s.tp1 > 0 and s.tp2 > 0 and abs(s.trigger - s.stop) / s.trigger <= 0.25
