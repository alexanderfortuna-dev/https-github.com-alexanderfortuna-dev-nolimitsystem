import copy, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import evaluate_setup as es
import backtest_strategy as bt

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
GOOD = json.load(open(os.path.join(EX, "valid_trade.json")))
BAD = json.load(open(os.path.join(EX, "rejected_trade.json")))


def setup(**ch):
    s = copy.deepcopy(GOOD["setup"])
    s.update(ch)
    return s


def with_registry(status):
    reg = {"strategies": [{"id": "TRX", "status": status, "evidence": {"net_r": 0.2}}]}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(reg, f); f.close()
    return f.name


def test_rubric_sums_to_100_and_has_seven_gates():
    assert sum(es.RUBRIC.values()) == 100 and len(es.GATES) == 7


def test_real_registry_has_no_validated_strategy_so_best_case_is_wait():
    r = es.evaluate(GOOD["setup"])
    assert r["decision"] == GOOD["expected_decision_today"] == "WAIT" and not r["executable"]
    assert r["validation_status"] == "RESEARCH_CANDIDATE"
    assert r["gates"]["out_of_sample_evidence"] is False            # caller's claim overridden by the registry
    assert r["breakdown"]["statistical_edge"]["points"] == 8 and r["score_is_not_a_probability"]


def test_trade_candidate_requires_validated_strategy_score_and_all_gates():
    orig = es.REGISTRY
    es.REGISTRY = with_registry("VALIDATED")
    try:
        r = es.evaluate(GOOD["setup"])
        assert r["decision"] == GOOD["expected_decision_if_strategy_validated"] == "TRADE CANDIDATE" and r["setup_score"] == 93
        assert "paper only" in r["execution_mode"]
        for g in es.GATES:                                          # any single failed gate removes the candidate
            gates = dict(GOOD["setup"]["gates"]); gates[g] = False
            assert es.evaluate(setup(gates=gates))["decision"] != "TRADE CANDIDATE", g
        low = dict(GOOD["setup"]["scores"], market_structure=5)
        assert es.evaluate(setup(scores=low))["decision"] == "WAIT"
        assert es.evaluate(setup(conflicting_signals=["1D up, 4H down"]))["decision"] == "WAIT"
    finally:
        es.REGISTRY = orig


def test_rejected_example_and_unknown_strategy():
    r = es.evaluate(BAD["setup"])
    assert r["decision"] == BAD["expected_decision"] == "INSUFFICIENT DATA" and not r["executable"]
    s = copy.deepcopy(BAD["setup"]); s["market_data_status"] = "VERIFIED"
    r = es.evaluate(s)
    assert r["decision"] == "NO TRADE" and r["validation_status"] == "REJECTED" and r["breakdown"]["statistical_edge"]["points"] == 0
    assert es.evaluate(setup(strategy="my-gut-feeling"))["decision"] == "NO TRADE"


def test_missing_information_fails_closed():
    s = setup(); del s["market_data_status"]
    assert es.evaluate(s)["decision"] == "INSUFFICIENT DATA"
    sc = dict(GOOD["setup"]["scores"]); del sc["liquidity"]
    assert es.evaluate(setup(scores=sc))["decision"] == "INSUFFICIENT DATA"
    assert es.evaluate({})["decision"] == "INSUFFICIENT DATA"
    assert es.evaluate(setup(stop=None))["gates"]["objective_entry_and_invalidation"] is False
    hi = dict(GOOD["setup"]["scores"], liquidity=999)
    r = es.evaluate(setup(scores=hi))
    assert r["breakdown"]["liquidity"]["points"] == 10 and r["problems"]


def synthetic(n=1500, drift=0.0006, seed=5):
    import math, random
    rng, px, rows = random.Random(seed), 100.0, []
    for i in range(n):
        o = px
        px = px * math.exp(drift + rng.gauss(0, 0.012))
        rows.append({"ts": i * 14_400_000.0, "open": o, "high": max(o, px) * (1 + abs(rng.gauss(0, 0.003))),
                     "low": min(o, px) * (1 - abs(rng.gauss(0, 0.003))), "close": px})
    return rows


def test_backtest_is_deterministic_causal_and_costs_hurt():
    rows = synthetic()
    a = bt.run(rows, "TRX", 0.0006, 0.0002, 0.0002, 0.0001, 4, False, 7)
    b = bt.run(rows, "TRX", 0.0006, 0.0002, 0.0002, 0.0001, 4, False, 7)
    assert a == b and a["all"]["trades"] > 10
    cs = a["cost_sensitivity"]
    assert cs["1.0x"] > cs["1.5x"] > cs["2.0x"]
    s_full, _ = bt.setups(rows, "TRX")
    changed = [dict(r) for r in rows]
    for r in changed[1000:]:
        for k in ("open", "high", "low", "close"):
            r[k] *= 3
    s_alt, _ = bt.setups(changed, "TRX")
    assert [s for s in s_full if s["i"] < 1000] == [s for s in s_alt if s["i"] < 1000]
    assert all((s["trigger"] - s["stop"]) * s["side"] > 0 for s in s_full)
    assert all(s["side"] == 1 for s in bt.setups(rows, "TRX", long_only=True)[0])


def test_backtest_rejects_bad_data():
    rows = synthetic(300)
    assert not bt.check_data(rows, 14_400_000)["ok"]                 # too short
    rows = synthetic(600); rows[10]["high"] = 0.01
    assert not bt.check_data(rows, 14_400_000)["ok"]
    rows = synthetic(600); rows[5], rows[6] = rows[6], rows[5]
    assert not bt.check_data(rows, 14_400_000)["ok"]


def test_validator_catches_invalid_yaml_frontmatter():
    """Regression: a colon inside an unquoted description once broke installation."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fm = re.match(r"^---\n(.*?)\n---\n", open(os.path.join(root, "SKILL.md")).read(), re.S).group(1)
    for line in fm.splitlines():
        key, _, value = line.partition(":")
        assert ": " not in value.strip() and " #" not in value, key
