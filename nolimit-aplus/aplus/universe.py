"""Eligible instrument universe. Products are NOT assumed to share rules:
each asset group has its own liquidity floor, slippage tier and validation track."""
from __future__ import annotations

from dataclasses import dataclass

from .bitget.client import BitgetClient
from .bitget.models import Instrument

CATEGORY = "USDT-FUTURES"
MIN_TURNOVER_USD = 20_000_000          # 24h turnover floor for eligibility
MAX_PER_GROUP = {"crypto_major": 2, "crypto_alt": 18, "commodity": 8, "equity": 20, "index_fx": 6}
MAJORS = {"BTCUSDT", "ETHUSDT"}

# Products the owner holds that are NOT covered, with the reason. Shown on the dashboard.
UNSUPPORTED = {
    "TradFi CFD": "Bitget documents a separate /api/v3/cfd/* family (own CFD account, symbols like XAUUSD.s). "
                  "The audit READS the CFD account. It is excluded from scanning/execution: no Demo Trading "
                  "coverage is documented and CFD orders have no clientOid, so duplicate-order prevention "
                  "cannot be guaranteed.",
    "Grid bots (spot/futures)": "Bot detail needs a known botId (no list endpoint). The audit reads the bot "
                                "account balance via /api/v2/account/bot-assets and per-bot results for botIds "
                                "listed in APLUS_GRID_BOT_IDS. Bots are never modified.",
    "Index / FX perps": "Listed, but below the 24h turnover floor at the time of the scan.",
}


@dataclass(frozen=True)
class UniverseMember:
    instrument: Instrument
    group: str
    turnover_24h: float
    last_price: float
    funding_rate: float


def group_of(inst: Instrument) -> str:
    cls = inst.asset_class
    if cls in ("metal", "energy", "commodity"):
        return "commodity"
    if cls in ("stock", "etf"):
        return "equity"
    if cls in ("index", "fx"):
        return "index_fx"
    return "crypto_major" if inst.symbol in MAJORS else "crypto_alt"


def build_universe(client: BitgetClient, min_turnover: float = MIN_TURNOVER_USD) -> list[UniverseMember]:
    insts = {d["symbol"]: Instrument.from_api(d) for d in client.instruments(CATEGORY)}
    ticks = {d["symbol"]: d for d in client.tickers(CATEGORY)}
    members: list[UniverseMember] = []
    for sym, inst in insts.items():
        t = ticks.get(sym)
        if not t or inst.status != "online" or inst.quote != "USDT":
            continue
        turnover = float(t.get("turnover24h") or 0)
        if turnover < min_turnover:
            continue
        members.append(UniverseMember(inst, group_of(inst), turnover, float(t.get("lastPrice") or 0),
                                      float(t.get("fundingRate") or 0)))
    members.sort(key=lambda m: -m.turnover_24h)
    out, counts = [], {}
    for m in members:
        if counts.get(m.group, 0) >= MAX_PER_GROUP[m.group]:
            continue
        counts[m.group] = counts.get(m.group, 0) + 1
        out.append(m)
    return out
