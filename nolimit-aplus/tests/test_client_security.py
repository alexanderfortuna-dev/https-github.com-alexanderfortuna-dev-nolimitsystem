import base64, hashlib, hmac, json

import pytest

from aplus import LIVE_TRADING_ENABLED
from aplus.bitget.client import BitgetClient, ProductionTradingDisabled, DEMO_WRITE_ALLOWLIST
from aplus.config import ApiCredentials, RiskLimits, Settings

CREDS = ApiCredentials("bg_key_1234", "s3cr3t-value", "pass-phrase")


class FakeResp:
    status_code = 200
    def __init__(self, data): self._d = data
    def json(self): return {"code": "00000", "msg": "success", "data": self._d}


class FakeSession:
    def __init__(self): self.calls = []
    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        return FakeResp({"orderId": "1", "clientOid": "x"})


def test_live_trading_constant_is_false():
    assert LIVE_TRADING_ENABLED is False


def test_signature_matches_documented_algorithm():
    ts, path, query, body = "1700000000000", "/api/v3/trade/place-order", "", '{"symbol":"BTCUSDT"}'
    expect = base64.b64encode(hmac.new(b"s3cr3t-value", (ts + "POST" + path + body).encode(), hashlib.sha256).digest()).decode()
    assert BitgetClient.sign("s3cr3t-value", ts, "post", path, query, body) == expect
    q = "category=USDT-FUTURES&symbol=BTCUSDT"
    expect = base64.b64encode(hmac.new(b"s3cr3t-value", (ts + "GET" + "/p" + "?" + q).encode(), hashlib.sha256).digest()).decode()
    assert BitgetClient.sign("s3cr3t-value", ts, "GET", "/p", q) == expect


@pytest.mark.parametrize("path", sorted(DEMO_WRITE_ALLOWLIST) + ["/api/v3/trade/close-positions", "/api/v3/account/switch"])
def test_production_client_cannot_send_any_write(path):
    s = FakeSession()
    c = BitgetClient(CREDS, demo=False, session=s)
    with pytest.raises(ProductionTradingDisabled):
        c.post(path, {"symbol": "BTCUSDT"})
    assert s.calls == []                      # blocked before any network I/O


@pytest.mark.parametrize("path", ["/api/v3/trade/close-positions", "/api/v3/account/set-hold-mode", "/api/v3/account/switch",
                                  "/api/v3/trade/grid/close-bot", "/api/v3/cfd/trade/place-order", "/api/v2/spot/wallet/withdrawal"])
def test_demo_client_blocks_paths_outside_allowlist(path):
    s = FakeSession()
    with pytest.raises(ProductionTradingDisabled):
        BitgetClient(CREDS, demo=True, session=s).post(path, {})
    assert s.calls == []


def test_demo_write_carries_paptrading_header_and_signed_body():
    s = FakeSession()
    BitgetClient(CREDS, demo=True, session=s).place_order({"symbol": "BTCUSDT", "qty": "0.01"})
    call = s.calls[0]
    assert call["headers"]["paptrading"] == "1"
    assert call["headers"]["ACCESS-KEY"] == "bg_key_1234"
    ts = call["headers"]["ACCESS-TIMESTAMP"]
    assert call["headers"]["ACCESS-SIGN"] == BitgetClient.sign("s3cr3t-value", ts, "POST", "/api/v3/trade/place-order", "", call["data"])
    assert json.loads(call["data"]) == {"symbol": "BTCUSDT", "qty": "0.01"}


def test_production_read_has_no_paptrading_header():
    s = FakeSession()
    BitgetClient(CREDS, session=s).account_assets()
    assert "paptrading" not in s.calls[0]["headers"] and s.calls[0]["method"] == "GET"


def test_secrets_never_appear_in_repr():
    assert "s3cr3t" not in repr(CREDS) and "pass-phrase" not in str(CREDS) and "bg_key_1" not in repr(CREDS)


def test_no_live_execution_mode(monkeypatch):
    monkeypatch.setenv("APLUS_EXECUTION_MODE", "live")
    with pytest.raises(ValueError, match="Production trading is disabled"):
        Settings.from_env(dotenv=False)


def test_risk_limits_can_only_be_tightened(monkeypatch):
    monkeypatch.setenv("APLUS_RISK_PER_TRADE", "0.05")      # attempt to loosen 10x
    monkeypatch.setenv("APLUS_MAX_GROSS_EXPOSURE", "1.5")   # tighten
    lim = RiskLimits.from_env()
    assert lim.risk_per_trade == 0.005 and lim.max_gross_exposure == 1.5
