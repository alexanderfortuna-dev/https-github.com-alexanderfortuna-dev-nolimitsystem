import time

import pytest

from aplus.bitget.models import Instrument
from aplus.config import RiskLimits
from aplus.execution.broker import PaperBroker
from aplus.execution.engine import ExecutionEngine
from aplus.risk import AccountState, KillSwitch, RiskEngine
from aplus.store import SQLiteStore


def make_inst(symbol="BTCUSDT", tick=0.1, step=0.0001, min_qty=0.0001, max_lev=150.0, **kw) -> Instrument:
    d = dict(symbol=symbol, category="USDT-FUTURES", base=symbol[:-4], quote="USDT", asset_class="crypto", is_rwa=False,
             status="online", price_tick=tick, qty_step=step, min_qty=min_qty, max_qty=1200.0, max_market_qty=220.0,
             min_notional=5.0, max_leverage=max_lev, maker_fee=0.0002, taker_fee=0.0006, funding_interval_h=8.0,
             launch_time_ms=0)
    d.update(kw)
    return Instrument(**d)


@pytest.fixture
def inst():
    return make_inst()


@pytest.fixture
def limits():
    return RiskLimits()


def fresh_account(equity=10_000.0, **kw) -> AccountState:
    d = dict(equity=equity, available=equity, ts=time.time(), complete=True)
    d.update(kw)
    return AccountState(**d)


class Market:
    """Controllable price feed for the paper broker."""

    def __init__(self, **prices):
        self.px = dict(prices)
        self.age = 0.0

    def __call__(self, symbol):
        return self.px[symbol], time.time() - self.age


@pytest.fixture
def rig(tmp_path, inst, limits):
    market = Market(BTCUSDT=59_000.0)
    store = SQLiteStore(":memory:")
    kill = KillSwitch(tmp_path / "KILL")
    risk = RiskEngine(limits, kill)
    broker = PaperBroker(market, starting_equity=10_000.0, slippage=0.0)
    engine = ExecutionEngine(broker, store, risk, kill, {"BTCUSDT": inst}, limits)

    class Rig:
        pass

    r = Rig()
    r.market, r.store, r.kill, r.risk, r.broker, r.engine, r.inst = market, store, kill, risk, broker, engine, inst

    def plan(entry=60_000.0, stop=59_400.0, side=1, signal_ts=1):
        dec = risk.evaluate(inst=inst, strategy="TPB", interval="4H", side=side, signal_ts=signal_ts, entry=entry,
                            stop=stop, acct=engine.account_state(), slippage=0.0002, ticker_age_s=0.5,
                            validation_status="OOS_PASSED_FORWARD_PENDING")
        assert dec.approved, dec.reasons
        return dec.plan

    def arm(**kw):
        p = plan(**kw)
        key = f"BTCUSDT|TPB|4H|{p.signal_ts}"
        assert engine.submit(p, key, int(time.time() * 1000) + 3_600_000) == "ARMED"
        return p, f"paper|{key}"

    def trade(key):
        return store.one("trades", {"trade_key": key})

    r.plan, r.arm, r.trade = plan, arm, trade
    return r
