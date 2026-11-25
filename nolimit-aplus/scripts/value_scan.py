"""Value + setup scanner for the crypto-spot-compounder v4.0 rules (spot, top-250 by market cap).

Data (all public, no keys):
  CoinGecko  /coins/markets           market cap, FDV, supply, 7D/30D change
  DefiLlama  /overview/fees           fees, revenue, holders revenue (buybacks/fee share) per protocol
             /lite/protocols2         gecko_id mapping, TVL, category
  Bitget     SPOT daily + 4H candles  price structure, volume, RS vs BTC (closed bars only)

Computes the regime (v4.0 §2), setups S1 and S4 (§3), the six-pillar score (§4) for the pillars that
public data can support, and a valuation table (market cap / annualised revenue). Pillars that need a
dated news source (N driver, M, D) are left at 0 and marked NEEDS_SOURCE: UNKNOWN is not PASS.

Run: PYTHONPATH=. python scripts/value_scan.py  ->  reports/value_scan_<date>.json
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DAY = 86_400_000
STABLE_OR_WRAPPED = {"usdt", "usdc", "dai", "fdusd", "tusd", "usde", "usds", "pyusd", "usdd", "susde", "buidl", "usd1",
                     "usdtb", "usdy", "usdf", "rlusd", "gho", "frax", "lusd", "eurc", "xaut", "paxg", "wbtc", "weth",
                     "steth", "wsteth", "weeth", "cbbtc", "reth", "wbeth", "lbtc", "solvbtc", "bnsol", "jitosol",
                     "msol", "tbtc", "susds", "syrupusdc", "usdg", "bfusd", "usd0", "ustb", "ousg", "usyc"}
UA = {"User-Agent": "nolimit-value-scan/1.0"}


def get_json(url: str, tries: int = 3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def bitget(path: str):
    body = get_json("https://api.bitget.com" + path)
    if body.get("code") != "00000":
        raise RuntimeError(body.get("msg"))
    return body["data"]


def spot_daily(symbol: str, days: int, now_ms: int) -> list[dict]:
    seen, end = {}, now_ms
    while len(seen) < days:
        data = bitget(f"/api/v3/market/history-candles?category=SPOT&symbol={symbol}&interval=1D&limit=100&endTime={end}")
        if not data:
            break
        for d in data:
            seen[int(d[0])] = d
        oldest = min(int(d[0]) for d in data)
        if oldest >= end:
            break
        end = oldest
        time.sleep(0.05)
    rows = [{"ts": k, "o": float(v[1]), "h": float(v[2]), "l": float(v[3]), "c": float(v[4]), "v": float(v[6])}
            for k, v in sorted(seen.items()) if k + DAY <= now_ms]              # closed bars, quote volume
    return rows[-days:]


def spot_4h(symbol: str, now_ms: int) -> list[dict]:
    data = bitget(f"/api/v3/market/candles?category=SPOT&symbol={symbol}&interval=4H&limit=300")
    rows = [{"ts": int(d[0]), "o": float(d[1]), "h": float(d[2]), "l": float(d[3]), "c": float(d[4])} for d in data]
    return sorted([r for r in rows if r["ts"] + 4 * 3_600_000 <= now_ms], key=lambda r: r["ts"])


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(closes: list[float], n: int = 14) -> list[float]:
    out = [math.nan] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            gains += g; losses += l
            if i == n:
                gains /= n; losses /= n
                out[i] = 100 - 100 / (1 + gains / losses) if losses else 100
        else:
            gains = (gains * (n - 1) + g) / n
            losses = (losses * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + gains / losses) if losses else 100
    return out


def ret(rows: list[dict], n: int) -> float | None:
    return rows[-1]["c"] / rows[-1 - n]["c"] - 1 if len(rows) > n else None


def regime(btc: list[dict]) -> dict:
    c = [r["c"] for r in btc]
    s20, s50 = sma(c, 20), sma(c, 50)
    new_low = any(c[i] <= min(c[i - 10:i]) for i in range(len(c) - 3, len(c)))
    if new_low:
        name = "RISK-OFF"
    elif c[-1] > s20 and c[-1] > s50:
        name = "RISK-ON"
    elif c[-1] > s20:
        name = "SELEKTIF"
    else:
        name = "DEFENSIF"
    return {"regime": name, "btc_close": c[-1], "sma20": round(s20, 2), "sma50": round(s50, 2),
            "new_10d_closing_low_last_3d": new_low, "last_closed_day_utc": time.strftime("%Y-%m-%d", time.gmtime(btc[-1]["ts"] / 1000))}


def s1(rows: list[dict]) -> dict:
    """S1 DISCOUNT RECLAIM conditions on closed daily bars."""
    c = [r["c"] for r in rows]
    w90 = rows[-90:]
    hi90, lo90 = max(r["h"] for r in w90), min(r["l"] for r in w90)
    last10 = rows[-10:]
    cap_low = min(r["l"] for r in last10)
    a = cap_low <= hi90 * 0.65 and c[-1] <= lo90 * 1.40
    vols = [r["v"] for r in rows]
    rs = rsi(c)
    cap_days = []
    for i in range(len(rows) - 10, len(rows)):
        avg30 = sum(vols[i - 30:i]) / 30
        down = rows[i]["c"] < rows[i]["o"]
        if (down and vols[i] >= 2 * avg30) or (not math.isnan(rs[i]) and rs[i] < 30):
            cap_days.append(time.strftime("%m-%d", time.gmtime(rows[i]["ts"] / 1000)))
    b = bool(cap_days)
    s20 = sma(c, 20)
    prior5 = max(r["h"] for r in rows[-6:-1])
    trig = max(s20, prior5)
    cfired = c[-1] > s20 and c[-1] > prior5
    stop = cap_low
    return {"a_discount": a, "drop_from_90d_high_at_low": round(1 - cap_low / hi90, 4), "above_90d_low": round(c[-1] / lo90 - 1, 4),
            "b_capitulation": b, "capitulation_days": cap_days, "c_trigger_fired": cfired,
            "trigger_close_above": round(trig, 8), "distance_to_trigger": round(trig / c[-1] - 1, 4),
            "stop": stop, "stop_width": round(1 - stop / max(c[-1], trig), 4),
            "conditions_met": int(a) + int(b) + int(cfired), "rsi14": round(rs[-1], 1)}


def s4(rows: list[dict], btc: list[dict], h4: list[dict]) -> dict:
    rs20 = (ret(rows, 20) or 0) - (ret(btc, 20) or 0)
    rs25 = (ret(rows, 25) or 0) - (ret(btc, 25) or 0)
    a = max(rs20, rs25) >= 0.08
    lo_now, lo_prev = min(r["l"] for r in rows[-10:]), min(r["l"] for r in rows[-20:-10])
    blo_now, blo_prev = min(r["l"] for r in btc[-10:]), min(r["l"] for r in btc[-20:-10])
    dd_a = 1 - lo_now / max(r["h"] for r in rows[-20:])
    dd_b = 1 - blo_now / max(r["h"] for r in btc[-20:])
    b = (lo_now > lo_prev and blo_now < blo_prev) or dd_a < 0.5 * dd_b
    out = {"rs20_vs_btc": round(rs20, 4), "rs25_vs_btc": round(rs25, 4), "a_relative_strength": a,
           "b_held_up": b, "asset_drawdown_20d": round(dd_a, 4), "btc_drawdown_20d": round(dd_b, 4)}
    if len(h4) >= 30:
        hi3d = max(r["h"] for r in h4[-19:-1])
        fired = h4[-1]["c"] > hi3d
        swing = min(r["l"] for r in h4[-12:])
        width = 1 - swing / max(h4[-1]["c"], hi3d)
        out.update(c_trigger_fired=fired, trigger_4h_close_above=hi3d, distance_to_trigger=round(hi3d / h4[-1]["c"] - 1, 4),
                   d_stop=swing, d_stop_width=round(width, 4), d_ok=width <= 0.10)
    else:
        out.update(c_trigger_fired=False, d_ok=False, note="4H history too short")
    out["conditions_met"] = int(a) + int(b) + int(out["c_trigger_fired"]) + int(out["d_ok"])
    return out


def main() -> int:
    now_ms = int(time.time() * 1000)
    out_dir = Path("reports")
    cg = get_json("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&price_change_percentage=7d,30d")
    lite = get_json("https://api.llama.fi/lite/protocols2")
    fees = get_json("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")["protocols"]
    rev = get_json("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue")["protocols"]
    hold = get_json("https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyHoldersRevenue")["protocols"]
    spot = {t["symbol"]: t for t in bitget("/api/v3/market/tickers?category=SPOT")}

    # gecko_id -> protocol ids (parents and stand-alone protocols)
    gid_of = {p["id"]: p.get("gecko_id") for p in lite["parentProtocols"]}
    tvl, cat = {}, {}
    for p in lite["protocols"]:
        g = p.get("geckoId") or p.get("gecko_id") or gid_of.get(p.get("parentProtocol"))
        if g:
            tvl.setdefault(g, [0.0, 0.0])
            t = p.get("tvl") or 0
            tvl[g][0] += t
            ch = p.get("change_1m")
            tvl[g][1] += t / (1 + ch / 100) if ch not in (None, -100) else t
            cat.setdefault(g, p.get("category"))

    def agg(rows: list[dict]) -> dict:
        res = {}
        for p in rows:
            g = gid_of.get(p.get("parentProtocol")) if p.get("parentProtocol") else None
            g = g or next((x.get("gecko_id") for x in lite["parentProtocols"] if x["name"] == p.get("name")), None)
            if not g:
                continue
            a = res.setdefault(g, {"d30": 0.0, "p30": 0.0, "y1": 0.0})
            a["d30"] += p.get("total30d") or 0
            a["p30"] += p.get("total60dto30d") or 0
            a["y1"] += p.get("total1y") or 0
        return res
    # stand-alone protocols carry gecko_id only in /protocols; build the name map once
    name_gid = {p["name"]: (p.get("geckoId") or p.get("gecko_id")) for p in lite["protocols"]}
    gid_of.update({k: v for k, v in gid_of.items()})
    for rows in (fees, rev, hold):
        for p in rows:
            if not p.get("parentProtocol") and name_gid.get(p.get("name")):
                p["parentProtocol"] = "self#" + p["name"]
                gid_of["self#" + p["name"]] = name_gid[p["name"]]
    F, R, H = agg(fees), agg(rev), agg(hold)

    universe = []
    for coin in cg:
        sym = coin["symbol"].lower()
        if sym in STABLE_OR_WRAPPED or any(w in coin["name"].lower() for w in ("wrapped", "staked", "bridged", "usd")):
            continue
        pair = sym.upper() + "USDT"
        if pair in spot:
            universe.append((coin, pair))
    # Two CoinGecko coins can share a ticker (e.g. AERO). The exchange pair is then ambiguous: drop both.
    counts = {}
    for _, pair in universe:
        counts[pair] = counts.get(pair, 0) + 1
    ambiguous = sorted(p for p, n in counts.items() if n > 1)
    universe = [(c, p) for c, p in universe if counts[p] == 1]

    btc = spot_daily("BTCUSDT", 200, now_ms)
    reg = regime(btc)

    def one(item):
        coin, pair = item
        try:
            d = spot_daily(pair, 200, now_ms)
            if len(d) < 180:
                return {"symbol": pair, "skip": f"only {len(d)} daily bars (<180)"}
            h4 = spot_4h(pair, now_ms)
        except Exception as exc:  # noqa: BLE001
            return {"symbol": pair, "skip": f"data error {type(exc).__name__}"}
        return {"symbol": pair, "coin": coin, "d": d, "h4": h4}

    with ThreadPoolExecutor(6) as ex:
        fetched = list(ex.map(one, universe))

    results, skipped = [], []
    for f in fetched:
        if "skip" in f:
            skipped.append(f)
            continue
        coin, d, h4 = f["coin"], f["d"], f["h4"]
        g = coin["id"]
        mcap, fdv = coin.get("market_cap") or 0, coin.get("fully_diluted_valuation") or 0
        s1r, s4r = s1(d), s4(d, btc, h4)
        rs7 = (ret(d, 7) or 0) - (ret(btc, 7) or 0)
        rs20 = s4r["rs20_vs_btc"]
        vol_share_now = sum(r["v"] for r in d[-7:]) / max(sum(r["v"] for r in btc[-7:]), 1)
        vol_share_prev = sum(r["v"] for r in d[-14:-7]) / max(sum(r["v"] for r in btc[-14:-7]), 1)
        rv, fe, ho = R.get(g), F.get(g), H.get(g)
        ann_rev = (rv["d30"] * 12.17) if rv else 0.0
        ann_hold = (ho["d30"] * 12.17) if ho else 0.0
        tv = tvl.get(g)
        tvl_chg = (tv[0] / tv[1] - 1) if tv and tv[1] else None

        # ---- pillars (0-2). Only what public data supports; the rest is NEEDS_SOURCE.
        live_setup = s1r["c_trigger_fired"] and s1r["a_discount"] and s1r["b_capitulation"] or \
                     (s4r["a_relative_strength"] and s4r["b_held_up"] and s4r["c_trigger_fired"] and s4r["d_ok"])
        best_stop_ok = (s1r["stop_width"] <= 0.12) or s4r.get("d_ok", False)
        T = 2 if (live_setup and rs20 >= 0 and best_stop_ok) else 1 if (rs20 >= 0 and best_stop_ok) else 0
        rising = bool(rv and rv["d30"] > rv["p30"] > 0)
        accrues = ann_hold > 0
        Fp = 2 if (rising and accrues) else 1 if (rising or accrues) else 0
        W_extra = (tvl_chg is not None and tvl_chg >= 0.20) or (mcap and ann_hold >= 0.01 * mcap)
        Wp = 2 if (rs7 > 0 and rs20 > 0 and vol_share_now > vol_share_prev and W_extra) else 1 if (rs7 > 0 and rs20 > 0) else 0
        pillars = {"T": T, "F": Fp, "N": 0, "M": 0, "D": 0, "W": Wp}
        results.append({
            "symbol": f["symbol"], "name": coin["name"], "gecko_id": g, "rank": coin.get("market_cap_rank"),
            "price": d[-1]["c"], "market_cap": mcap, "fdv": fdv, "fdv_to_mcap": round(fdv / mcap, 2) if mcap and fdv else None,
            "chg_7d": coin.get("price_change_percentage_7d_in_currency"), "chg_30d": coin.get("price_change_percentage_30d_in_currency"),
            "category": cat.get(g), "rs7_vs_btc": round(rs7, 4), "rs20_vs_btc": rs20,
            "volume_share_rising": vol_share_now > vol_share_prev,
            "revenue_30d": round(rv["d30"]) if rv else None, "revenue_prev30d": round(rv["p30"]) if rv else None,
            "fees_30d": round(fe["d30"]) if fe else None, "holders_revenue_annualised": round(ann_hold) if ho else None,
            "mcap_to_annual_revenue": round(mcap / ann_rev, 1) if ann_rev > 0 and mcap else None,
            "buyback_yield": round(ann_hold / mcap, 4) if mcap and ann_hold else None, "tvl_change_30d": round(tvl_chg, 4) if tvl_chg is not None else None,
            "s1": s1r, "s4": s4r, "pillars": pillars, "pillar_total": sum(pillars.values()),
            "needs_source": ["N driver (dated)", "M shipped upgrade / no exploit / no regulatory action", "D listing/ETF/integration",
                             "supply unlocks next 90D"],
        })
    results.sort(key=lambda r: (-r["pillar_total"], min(r["s1"]["distance_to_trigger"] if r["s1"]["conditions_met"] >= 2 else 9,
                                                         r["s4"].get("distance_to_trigger", 9) if r["s4"]["conditions_met"] >= 3 else 9)))
    report = {"generated_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "regime": reg,
              "coverage": {"coingecko_top": len(cg), "ambiguous_ticker_dropped": ambiguous, "tradable_on_bitget_spot": len(universe), "scored": len(results), "skipped": skipped},
              "results": results}
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"value_scan_{time.strftime('%Y%m%d', time.gmtime())}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
