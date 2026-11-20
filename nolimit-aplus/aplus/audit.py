"""Read-only account audit with explicit double-count prevention.

Ledger model. Bitget shows the same money in several views; each USDT is counted in
exactly ONE ledger below:

  L1 unified   /api/v3/account/assets            accountEquity (already includes spot holdings,
                                                 futures margin and unrealised P&L of the UTA)
  L2 funding   /api/v3/account/funding-assets    priced with public spot tickers
  L3 bots      /api/v2/account/bot-assets        strategy (grid bot) accounts: futures + spot
  L4 cfd       /api/v3/cfd/account/fund-detail   TradFi CFD account equity

  TOTAL = L1 + L2 + L3 + L4.
  Never added on top: spot assets, futures balance, unrealised P&L (inside L1); the app's
  "Overview" figure (it is the sum itself). /api/v2/account/all-account-balance is used
  only as an independent cross-check of the total.

The audit performs GET requests only and refuses to run with a key that can trade or withdraw.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict

from .bitget.client import BitgetAPIError, BitgetClient

STABLES = {"USDT": 1.0, "USDC": 1.0}
FUTURES_CATEGORIES = ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES")
CROSS_CHECK_TOLERANCE = 0.03


@dataclass
class AuditReport:
    ts: float
    mode: str
    complete: bool = True
    issues: list[str] = field(default_factory=list)
    key_permissions: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    ledgers: dict = field(default_factory=dict)          # name -> usd value (None if unavailable)
    total_equity: float = 0.0
    unified: dict = field(default_factory=dict)
    available_usdt: float = 0.0
    liabilities_usd: float = 0.0
    spot_assets: list[dict] = field(default_factory=list)
    funding_assets: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    gross_exposure: float = 0.0
    effective_leverage: float = 0.0
    unrealized_pnl: float = 0.0
    pending_orders: list[dict] = field(default_factory=list)
    pending_strategy_orders: list[dict] = field(default_factory=list)
    bots: dict = field(default_factory=dict)
    cfd: dict = field(default_factory=dict)
    fees: dict = field(default_factory=dict)
    cross_check: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def assert_read_only(info: dict) -> list[str]:
    """Return a list of violations. Empty list = key is least-privilege."""
    problems = []
    perm_type = str(info.get("permType", "")).lower()
    perms = [str(p).lower() for p in info.get("permissions") or []]
    if perm_type != "read-only":
        problems.append(f"permType is {perm_type or 'unknown'!r}, expected 'read-only'")
    if "withdraw" in perms:
        problems.append("key carries WITHDRAW permission")
    if not info.get("ips"):
        problems.append("no IP whitelist bound to this key (recommended, not blocking)")
    return problems


def run_audit(client: BitgetClient, *, mode: str = "production_readonly", manual_bots_usdt: float | None = None,
              spot_prices: dict[str, float] | None = None) -> AuditReport:
    rep = AuditReport(ts=time.time(), mode=mode)

    def required(name: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any failure makes the audit incomplete
            rep.complete = False
            rep.issues.append(f"{name}: {type(exc).__name__}: {getattr(exc, 'code', '')} {getattr(exc, 'msg', exc)}".strip())
            return default

    def optional(name: str, fn, default):
        try:
            return fn(), True
        except Exception as exc:  # noqa: BLE001
            rep.issues.append(f"{name} unavailable ({getattr(exc, 'code', type(exc).__name__)}): not counted")
            return default, False

    # ---- key permissions (least privilege)
    info = required("account_info", client.account_info, {})
    rep.key_permissions = {"permType": info.get("permType"), "permissions": info.get("permissions"),
                           "ip_whitelist": bool(info.get("ips"))}
    if mode == "production_readonly" and info:
        violations = [v for v in assert_read_only(info) if "recommended" not in v]
        if violations:
            rep.complete = False
            rep.issues.append("KEY NOT READ-ONLY: " + "; ".join(violations))
            return rep                      # stop immediately: do not operate with an over-privileged key
        rep.issues += [v for v in assert_read_only(info) if "recommended" in v]

    st = required("account_settings", client.account_settings, {})
    rep.settings = {k: st.get(k) for k in ("accountMode", "accountLevel", "assetMode", "holdMode", "stpMode", "repayMode")}
    sym_cfg = {(c.get("category"), c.get("symbol")): c for c in st.get("symbolConfigList") or []}

    # ---- L1 unified account
    ua = required("unified_assets", client.account_assets, {})
    rep.unified = {k: _f(ua.get(k)) for k in ("accountEquity", "usdtEquity", "unrealisedPnl", "effEquity", "mmr", "imr",
                                              "positionValue")}
    rep.unified["mgnRatio"] = ua.get("mgnRatio")
    assets = ua.get("assets") or []
    rep.spot_assets = [{"coin": a.get("coin"), "equity": _f(a.get("equity")), "usd_value": _f(a.get("usdValue")),
                        "balance": _f(a.get("balance")), "available": _f(a.get("available")),
                        "locked": _f(a.get("locked")), "debt": _f(a.get("debt"))} for a in assets]
    rep.spot_assets.sort(key=lambda a: -a["usd_value"])
    rep.available_usdt = next((a["available"] for a in rep.spot_assets if a["coin"] == "USDT"), 0.0)
    price_of = {a["coin"]: (a["usd_value"] / a["equity"]) for a in rep.spot_assets if a["equity"]}
    rep.liabilities_usd = sum(a["debt"] * price_of.get(a["coin"], STABLES.get(a["coin"], 0.0)) for a in rep.spot_assets)
    rep.ledgers["unified"] = rep.unified["accountEquity"] if ua else None

    # ---- L2 funding account (priced with spot tickers; unpriced coins are reported, not guessed)
    fa = required("funding_assets", client.funding_assets, [])
    prices = dict(STABLES)
    prices.update(price_of)
    if spot_prices:
        prices.update(spot_prices)
    elif fa:
        tick, ok = optional("spot_tickers", lambda: client.tickers("SPOT"), [])
        prices.update({t["symbol"][:-4]: _f(t.get("lastPrice")) for t in tick if t.get("symbol", "").endswith("USDT")})
    funding_total, unpriced = 0.0, []
    for a in fa:
        bal = _f(a.get("balance")) or (_f(a.get("available")) + _f(a.get("frozen")))
        px = prices.get(a.get("coin"))
        if px is None and bal > 0:
            unpriced.append(a.get("coin"))
        usd = bal * (px or 0.0)
        funding_total += usd
        rep.funding_assets.append({"coin": a.get("coin"), "balance": bal, "usd_value": usd, "priced": px is not None})
    if unpriced:
        rep.issues.append(f"funding coins without a USDT price (valued at 0): {', '.join(unpriced)}")
    rep.ledgers["funding"] = funding_total

    # ---- positions (all futures categories)
    for cat in FUTURES_CATEGORIES:
        plist = required(f"positions[{cat}]", lambda c=cat: client.positions(c), [])
        for p in plist:
            size = _f(p.get("total"))
            if size == 0:
                continue
            mark = _f(p.get("markPrice"))
            side = 1 if str(p.get("posSide", "")).lower() == "long" else -1
            notional = abs(size) * mark
            cfg = sym_cfg.get((cat, p.get("symbol")), {})
            rep.positions.append({
                "category": cat, "symbol": p.get("symbol"), "side": side, "size": size, "avg_price": _f(p.get("avgPrice")),
                "mark_price": mark, "notional": notional, "margin_mode": p.get("marginMode") or cfg.get("marginMode"),
                "exchange_leverage": _f(p.get("leverage") or cfg.get("leverage")),
                "margin": _f(p.get("positionBalance")), "unrealized_pnl": _f(p.get("unrealisedPnl")),
                "liquidation_price": _f(p.get("liquidationPrice")), "total_funding": _f(p.get("totalFunding")),
                "open_fee": _f(p.get("openFeeTotal")), "hold_mode": p.get("holdMode")})
    rep.gross_exposure = sum(p["notional"] for p in rep.positions)
    rep.unrealized_pnl = sum(p["unrealized_pnl"] for p in rep.positions)

    # ---- pending orders
    rep.pending_orders = [
        {k: o.get(k) for k in ("category", "symbol", "side", "orderType", "price", "qty", "cumExecQty", "orderStatus",
                               "reduceOnly", "posSide", "clientOid", "createdTime")}
        for o in required("open_orders", client.open_orders, [])]
    for cat in ("USDT-FUTURES", "SPOT"):
        so, _ = optional(f"strategy_orders[{cat}]", lambda c=cat: client.open_strategy_orders(c), [])
        rep.pending_strategy_orders += [
            {k: o.get(k) for k in ("category", "symbol", "type", "side", "qty", "status", "triggerPrice", "takeProfit",
                                   "stopLoss", "clientOid")} for o in so]

    # ---- L3 bot accounts (grid bots). Read-only; bots are never modified.
    bots_total, bots_ok = 0.0, True
    rep.bots = {"accounts": {}, "bots": [], "source": "api"}
    for acct_type in ("futures", "spot"):
        rows, ok = optional(f"bot_assets[{acct_type}]", lambda t=acct_type: client.bot_assets(t), [])
        bots_ok &= ok
        value = 0.0
        for r in rows:
            eq = _f(r.get("equity")) or (_f(r.get("available")) + _f(r.get("frozen")))
            value += eq * prices.get(r.get("coin"), 0.0)
        rep.bots["accounts"][acct_type] = value if ok else None
        bots_total += value
    for bot_id in [b.strip() for b in (os.environ.get("APLUS_GRID_BOT_IDS") or "").split(",") if b.strip()]:
        d, ok = optional(f"grid_bot[{bot_id}]", lambda b=bot_id: client.grid_bot_detail(b), {})
        if ok and d:
            rep.bots["bots"].append({k: d.get(k) for k in ("botId", "symbol", "status", "gridType", "totalProfit", "roi",
                                                           "gridProfit", "unpairedProfit", "margin", "minPrice", "maxPrice",
                                                           "gridNum", "runningTime")})
    if not bots_ok and manual_bots_usdt is not None:
        bots_total, rep.bots["source"] = float(manual_bots_usdt), "manual"
        rep.ledgers["bots"] = bots_total
    elif not bots_ok:
        rep.ledgers["bots"] = None
        rep.complete = False
        rep.issues.append("bot account balance could not be read: total equity is incomplete "
                          "(pass --bots-usdt to enter it manually)")
    else:
        rep.ledgers["bots"] = bots_total

    # ---- L4 CFD account (separate ledger; never part of unified equity)
    cfd, ok = optional("cfd_account", client.cfd_fund_detail, {})
    if ok and cfd:
        rep.cfd = {k: cfd.get(k) for k in ("equity", "balance", "credit", "marginUsed", "marginFree", "marginLevel",
                                           "pnl", "leverage", "currency", "status")}
        rep.ledgers["cfd"] = _f(cfd.get("equity")) - _f(cfd.get("credit"))
        pos, _ = optional("cfd_positions", client.cfd_positions, [])
        rep.cfd["positions"] = pos
    else:
        rep.ledgers["cfd"] = None            # not opened / not readable: reported, not assumed zero silently

    # ---- total (each ledger exactly once)
    rep.total_equity = sum(v for v in rep.ledgers.values() if v is not None)
    unified_eq = rep.unified.get("accountEquity") or 0.0
    rep.effective_leverage = rep.gross_exposure / unified_eq if unified_eq > 0 else 0.0

    # ---- independent cross-check against the classic overview
    overview, ok = optional("all_account_balance", client.all_account_balance, [])
    if ok and overview:
        ov_total = sum(_f(r.get("usdtBalance")) for r in overview)
        diff = (rep.total_equity - ov_total) / ov_total if ov_total else 0.0
        rep.cross_check = {"overview_total_usdt": ov_total, "ledger_total": rep.total_equity, "diff_pct": diff,
                           "by_account": {r.get("accountType"): _f(r.get("usdtBalance")) for r in overview},
                           "within_tolerance": abs(diff) <= CROSS_CHECK_TOLERANCE}
        if not rep.cross_check["within_tolerance"]:
            rep.issues.append(f"ledger total differs from exchange overview by {diff:.1%}: review ledgers before trusting equity")
    else:
        rep.cross_check = {"available": False}

    # ---- fees actually charged to this account
    for cat, sym in (("USDT-FUTURES", "BTCUSDT"), ("SPOT", "BTCUSDT")):
        fr, ok = optional(f"fee_rate[{cat}]", lambda c=cat, s=sym: client.fee_rate(c, s), {})
        if ok:
            rep.fees[cat] = {"maker": _f(fr.get("makerFeeRate")), "taker": _f(fr.get("takerFeeRate"))}
    return rep
