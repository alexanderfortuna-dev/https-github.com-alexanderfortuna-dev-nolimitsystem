import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import validate_market_data as vmd

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
GOOD = json.load(open(os.path.join(EX, "valid_trade.json")))["market_data"]
BAD = json.load(open(os.path.join(EX, "rejected_trade.json")))["market_data"]


def snap(**changes):
    s = copy.deepcopy(GOOD)
    s.update(changes)
    return s


def test_fresh_venue_quote_with_contract_is_verified():
    r = vmd.validate(GOOD)
    assert r["status"] == "VERIFIED" and r["reference_price"] == 59990.0
    q = r["quotes"][0]
    assert q["provider"] == "bitget-api-v3" and q["latency_s"] == 1.013 and q["freshness"] == "fresh" and q["spread_bps"] < 0.1


def test_stale_execution_quote_is_rejected():
    s = snap(now_ms=GOOD["now_ms"] + 60_000)
    r = vmd.validate(s)
    assert r["status"] == "STALE" and r["reference_price"] is None and r["contract"] is None


def test_same_quote_is_acceptable_for_research_only():
    s = snap(now_ms=GOOD["now_ms"] + 60_000, purpose="research")
    assert vmd.validate(s)["status"] == "VERIFIED"


def test_other_venue_price_cannot_stand_in_for_execution_venue():
    s = snap(quotes=[GOOD["quotes"][1]])
    r = vmd.validate(s)
    assert r["status"] != "VERIFIED" and any("execution venue" in e for e in r["errors"])


def test_wrong_contract_on_the_same_venue_is_a_conflict():
    r = vmd.validate(BAD)                      # XAUUSD.s CFD offered in place of the XAUUSDT perpetual, real quote stale
    assert r["status"] == "CONFLICT" and r["reference_price"] is None
    s = copy.deepcopy(BAD)
    s["quotes"][0]["timestamp_ms"] = s["now_ms"] - 2000
    r = vmd.validate(s)
    assert r["status"] == "CONFLICT" and any("different contract" in e for e in r["errors"])


def test_contract_spec_for_another_symbol_is_rejected():
    c = dict(GOOD["contract"], symbol="ETHUSDT")
    r = vmd.validate(snap(contract=c))
    assert r["status"] != "VERIFIED" and any("contract spec is for ETHUSDT" in e for e in r["errors"])


def test_sources_disagreeing_block_the_plan():
    s = copy.deepcopy(GOOD)
    s["quotes"][1].update(instrument="BTCUSDT", category="USDT-FUTURES", exchange="otherex", price=61000.0, bid=60999.0, ask=61001.0)
    r = vmd.validate(s)
    assert r["status"] == "CONFLICT" and r["max_source_disagreement"] > 0.005


def test_missing_data_fails_closed():
    assert vmd.validate({"execution_venue": "bitget", "instrument": "BTCUSDT", "quotes": []})["status"] == "INSUFFICIENT_DATA"
    assert vmd.validate(snap(contract=None))["status"] == "INSUFFICIENT_DATA"
    q = copy.deepcopy(GOOD["quotes"][:1]); q[0]["error"] = "HTTP 429"
    assert vmd.validate(snap(quotes=q))["status"] != "VERIFIED"
    q = copy.deepcopy(GOOD["quotes"][:1]); q[0].update(bid=60000.0, ask=59990.0)
    assert vmd.validate(snap(quotes=q))["status"] != "VERIFIED"
    q = copy.deepcopy(GOOD["quotes"][:1]); del q[0]["timestamp_ms"]
    assert vmd.validate(snap(quotes=q))["status"] != "VERIFIED"
    assert vmd.validate(snap(contract=dict(GOOD["contract"], status="offline")))["status"] != "VERIFIED"


def test_missing_connector_returns_insufficient_data(monkeypatch=None):
    orig = vmd.fetch_bitget
    def boom(symbol, category="USDT-FUTURES"):
        raise OSError("network unreachable")
    vmd.fetch_bitget = boom
    try:
        assert vmd.main(["x", "--fetch-bitget", "BTCUSDT"]) == 2
    finally:
        vmd.fetch_bitget = orig
