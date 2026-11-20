import json
import threading
import urllib.request

import pytest

from aplus.audit import run_audit
from aplus.bitget.client import BitgetAPIError, BitgetClient, ProductionTradingDisabled
from aplus.config import ApiCredentials
from aplus.execution.broker import BrokerError, client_oid
from aplus.execution.demo import DemoBroker
from aplus.store import DuplicateKey, SQLiteStore, now_iso
from conftest import make_inst


class FakeAccount:
    """Stands in for the read-only production client. Shapes follow the official v3 docs."""
    perm = {"permType": "read-only", "permissions": ["uta_mgt"], "ips": "1.2.3.4"}
    fail: set = set()

    def _g(self, name, value):
        if name in self.fail:
            raise BitgetAPIError("40037", "simulated failure", name)
        return value

    def account_info(self): return self._g("info", self.perm)
    def account_settings(self): return self._g("settings", {"accountMode": "unified", "holdMode": "one_way_mode", "symbolConfigList": []})
    def account_assets(self):
        # accountEquity 6000 ALREADY contains: USDT 4478 + ONDO spot 1200 + SUI spot 222 + futures uPnL 100
        return self._g("assets", {"accountEquity": "6000", "usdtEquity": "6000", "unrealisedPnl": "100", "assets": [
            {"coin": "USDT", "equity": "4578", "usdValue": "4578", "balance": "4478", "available": "4478", "locked": "0", "debt": "0"},
            {"coin": "ONDO", "equity": "2000", "usdValue": "1200", "balance": "2000", "available": "2000", "locked": "0", "debt": "0"},
            {"coin": "SUI", "equity": "300", "usdValue": "222", "balance": "300", "available": "300", "locked": "0", "debt": "0"}]})
    def funding_assets(self): return self._g("funding", [{"coin": "USDT", "balance": "500", "available": "500", "frozen": "0"},
                                                          {"coin": "FET", "balance": "1000", "available": "1000", "frozen": "0"}])
    def tickers(self, cat, symbol=None): return [{"symbol": "FETUSDT", "lastPrice": "0.5"}]
    def positions(self, cat):
        return self._g("positions", [{"symbol": "BTCUSDT", "posSide": "long", "total": "0.05", "avgPrice": "58000", "markPrice": "60000",
                                      "leverage": "20", "marginMode": "crossed", "unrealisedPnl": "100", "liquidationPrice": "30000",
                                      "positionBalance": "150", "holdMode": "one_way_mode"}] if cat == "USDT-FUTURES" else [])
    def open_orders(self): return self._g("orders", [{"symbol": "SUIUSDT", "side": "buy", "orderType": "limit", "price": "0.6", "qty": "100", "orderStatus": "live"}])
    def open_strategy_orders(self, cat, type_=None): return []
    def bot_assets(self, t): return self._g("bots", [{"coin": "USDT", "equity": "1500" if t == "futures" else "900"}])
    def grid_bot_detail(self, b): return {}
    def cfd_fund_detail(self): return self._g("cfd", {"equity": "300", "credit": "50", "balance": "250", "currency": "USD"})
    def cfd_positions(self): return []
    def all_account_balance(self): return self._g("overview", [{"accountType": "unified", "usdtBalance": "6000"}, {"accountType": "funding", "usdtBalance": "1000"},
                                                               {"accountType": "bot", "usdtBalance": "2400"}, {"accountType": "cfd", "usdtBalance": "250"}])
    def fee_rate(self, cat, sym): return {"makerFeeRate": "0.0002", "takerFeeRate": "0.0006"}


def test_audit_counts_each_ledger_once():
    r = run_audit(FakeAccount())
    assert r.complete
    assert r.ledgers == {"unified": 6000.0, "funding": 1000.0, "bots": 2400.0, "cfd": 250.0}
    # NOT 6000 + spot 1422 + futures 4578 + uPnL 100 ... : spot, futures margin and uPnL live inside `unified`
    assert r.total_equity == 9650.0
    assert r.available_usdt == 4478.0
    assert r.gross_exposure == 3000.0 and r.effective_leverage == pytest.approx(0.5)     # 20x SETTING, 0.5x effective
    assert r.positions[0]["exchange_leverage"] == 20.0
    assert r.cross_check["within_tolerance"] and len(r.pending_orders) == 1


def test_audit_refuses_keys_that_can_trade_or_withdraw():
    a = FakeAccount(); a.perm = {"permType": "read-and-write", "permissions": ["uta_trade", "withdraw"], "ips": "1.2.3.4"}
    r = run_audit(a)
    assert not r.complete and "KEY NOT READ-ONLY" in r.issues[0] and "WITHDRAW" in r.issues[0]
    assert r.total_equity == 0.0 and r.positions == []            # stopped before reading anything else


@pytest.mark.parametrize("broken", ["assets", "positions", "funding", "orders", "bots"])
def test_audit_is_incomplete_when_any_required_view_fails(broken):
    a = FakeAccount(); a.fail = {broken}
    r = run_audit(a)
    assert not r.complete and r.issues


def test_audit_manual_bot_capital_and_cross_check_mismatch():
    a = FakeAccount(); a.fail = {"bots"}
    r = run_audit(a, manual_bots_usdt=2400)
    assert r.complete and r.ledgers["bots"] == 2400 and r.bots["source"] == "manual"
    r = run_audit(a, manual_bots_usdt=9000)
    assert any("differs from exchange overview" in i for i in r.issues)


def test_audit_never_issues_a_write():
    class Guard(FakeAccount):
        def __getattr__(self, name):
            raise AssertionError(f"audit called unexpected method {name}")
    run_audit(Guard())


# ----------------------------------------------------------------------------- store constraints
def test_store_rejects_live_mode_and_non_reduce_only_exits():
    s = SQLiteStore(":memory:")
    base = dict(symbol="BTCUSDT", strategy="TPB", interval="4H", side=1, signal_ts=1, state="ARMED", plan={},
                created_at=now_iso(), updated_at=now_iso())
    with pytest.raises(Exception):
        s.insert("trades", {"trade_key": "live|x", "mode": "live", **base})
    s.insert("trades", {"trade_key": "paper|x", "mode": "paper", **base})
    with pytest.raises(DuplicateKey):
        s.insert("trades", {"trade_key": "paper|x", "mode": "paper", **base})
    o = dict(trade_key="paper|x", status="live", side="sell", qty=1.0, created_at=now_iso(), updated_at=now_iso())
    with pytest.raises(Exception):
        s.insert("orders", {"client_oid": "a", "role": "tp1", "reduce_only": False, **o})
    s.insert("orders", {"client_oid": "a", "role": "tp1", "reduce_only": True, **o})
    assert s.one("orders", {"client_oid": "a"})["reduce_only"] is True


def test_client_oid_is_deterministic_and_exchange_safe():
    import re
    a, b = client_oid("paper|BTCUSDT|TPB|4H|1", "entry"), client_oid("paper|BTCUSDT|TPB|4H|1", "entry")
    assert a == b != client_oid("paper|BTCUSDT|TPB|4H|1", "tp1") and re.fullmatch(r"[\.A-Z\:/a-z0-9_-]{1,32}", a)


# ----------------------------------------------------------------------------- demo broker request shapes
class Recorder:
    demo = True
    def __init__(self, hold="one_way_mode"): self.bodies, self.hold = [], hold
    def account_settings(self): return {"holdMode": self.hold}
    def place_order(self, body): self.bodies.append(body); return {"orderId": "9", "clientOid": body["clientOid"]}
    def order_info(self, client_oid=None, order_id=None):
        return {"orderId": "9", "orderStatus": "filled", "cumExecQty": "0.01", "avgPrice": "60000", "feeDetail": [{"feeCoin": "USDT", "fee": "-0.36"}]}
    def set_leverage(self, body): self.bodies.append(body)
    def open_strategy_orders(self, cat, type_=None): return [{"orderId": "77", "symbol": "BTCUSDT", "stopLoss": "59400", "status": "pending", "qty": "0.01"}]
    def modify_strategy_order(self, body): self.bodies.append(body); return {}


def test_demo_entry_carries_atomic_stop_isolated_margin_and_client_oid():
    c = Recorder(); b = DemoBroker(c); inst = make_inst()
    b.prepare(inst, 12)
    res = b.market_entry_with_stop(inst, 1, 0.0123456, 59_400.04, "apabc")
    lev, order = c.bodies
    assert lev == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "leverage": "12", "marginMode": "isolated"}
    assert order == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "qty": "0.0123", "side": "buy", "orderType": "market",
                     "clientOid": "apabc", "marginMode": "isolated", "reduceOnly": "no", "stopLoss": "59400",
                     "slTriggerBy": "mark", "slOrderType": "market"}
    assert res.status == "filled" and res.fee == 0.36 and b.protective_stop("BTCUSDT") == 59_400.0


def test_demo_exits_are_reduce_only():
    c = Recorder(); b = DemoBroker(c); inst = make_inst()
    b.reduce_limit(inst, -1, 0.005, 60_600, "tp")
    b.flatten(inst, 1, 0.01, "fl")
    assert all(x["reduceOnly"] == "yes" for x in c.bodies) and c.bodies[0]["orderType"] == "limit" and c.bodies[1]["side"] == "sell"
    assert b.move_stop(inst, 60_000) and c.bodies[-1] == {"orderId": "77", "qty": "0.01", "stopLoss": "60000"}


def test_demo_broker_refuses_production_client_and_hedge_mode():
    with pytest.raises(BrokerError, match="Production trading is disabled"):
        DemoBroker(BitgetClient(ApiCredentials("k", "s", "p"), demo=False))
    with pytest.raises(BrokerError, match="one_way_mode"):
        DemoBroker(Recorder(hold="hedge_mode")).market_entry_with_stop(make_inst(), 1, 0.01, 59_000, "x")


# ----------------------------------------------------------------------------- control API auth
def test_control_api_requires_token(rig, tmp_path):
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from aplus.server import make_handler
    app = SimpleNamespace(settings=SimpleNamespace(api_token="t" * 32), store=rig.store, engine=rig.engine,
                          tick=rig.engine.tick)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, token=None, method="POST"):
        req = urllib.request.Request(url + path, data=b"{}" if method == "POST" else None, method=method,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, {}
    try:
        assert call("/health", method="GET")[0] == 200
        assert call("/run/tick")[0] == 401 and call("/kill", "wrong")[0] == 401 and call("/status", method="GET")[0] == 401
        assert call("/run/tick", "t" * 32) == (200, {"ok": True})
        assert call("/tv/alert", "t" * 32)[1] == {"logged": True, "authorises_trade": False}
        assert call("/enable-live", "t" * 32)[0] == 404
        assert call("/kill", "t" * 32)[0] == 200 and rig.kill.active
    finally:
        srv.shutdown()
