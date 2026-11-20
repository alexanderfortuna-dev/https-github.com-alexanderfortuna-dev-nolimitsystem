#!/usr/bin/env python3
"""Market-data integrity gate. A trade plan may only be built from data this script marks VERIFIED.

Usage:
  python validate_market_data.py snapshot.json
  python validate_market_data.py --fetch-bitget BTCUSDT      (needs network access to api.bitget.com)

snapshot.json:
{
  "now_ms": 1795214104686,                 # optional; defaults to the local clock
  "execution_venue": "bitget",
  "instrument": "BTCUSDT", "category": "USDT-FUTURES",
  "purpose": "execution" | "research",
  "quotes": [
    {"provider": "bitget-api", "exchange": "bitget", "instrument": "BTCUSDT", "category": "USDT-FUTURES",
     "timestamp_ms": 1795214102987, "timezone": "UTC", "price": 59024.7, "bid": 59024.6, "ask": 59024.7,
     "error": null}
  ],
  "contract": {"symbol": "BTCUSDT", "status": "online", "price_tick": 0.1, "qty_step": 0.0001,
               "min_qty": 0.0001, "min_notional": 5, "max_leverage": 150, "taker_fee": 0.0006, "maker_fee": 0.0002}
}
Exit code 0 only when status == VERIFIED.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

from _common import emit, num

MAX_AGE_S = {"execution": 15.0, "research": 3600.0}
MAX_SPREAD_BPS = 15.0            # wider than this is flagged as poor liquidity
MAX_SOURCE_DISAGREEMENT = 0.005  # 0.5% between providers -> investigate before any plan
MAX_FUTURE_SKEW_S = 5.0


def fetch_bitget(symbol: str, category: str = "USDT-FUTURES") -> dict:
    def get(path: str):
        with urllib.request.urlopen("https://api.bitget.com" + path, timeout=15) as r:
            body = json.loads(r.read())
        if body.get("code") != "00000":
            raise RuntimeError(f"bitget {body.get('code')}: {body.get('msg')}")
        return body["data"]
    t = get(f"/api/v3/market/tickers?category={category}&symbol={symbol}")[0]
    i = get(f"/api/v3/market/instruments?category={category}&symbol={symbol}")[0]
    return {
        "now_ms": int(time.time() * 1000), "execution_venue": "bitget", "instrument": symbol, "category": category,
        "purpose": "execution",
        "quotes": [{"provider": "bitget-api-v3", "exchange": "bitget", "instrument": t["symbol"], "category": category,
                    "timestamp_ms": int(t["ts"]), "timezone": "UTC", "price": float(t["lastPrice"]),
                    "bid": float(t["bid1Price"]), "ask": float(t["ask1Price"]), "mark_price": float(t["markPrice"]),
                    "funding_rate": float(t.get("fundingRate") or 0), "turnover_24h": float(t.get("turnover24h") or 0),
                    "error": None}],
        "contract": {"symbol": i["symbol"], "status": i["status"], "price_tick": float(i["priceMultiplier"]),
                     "qty_step": float(i["quantityMultiplier"]), "min_qty": float(i["minOrderQty"]),
                     "min_notional": float(i["minOrderAmount"]), "max_leverage": float(i["maxLeverage"]),
                     "max_qty": float(i["maxOrderQty"]), "taker_fee": float(i["takerFeeRate"]),
                     "maker_fee": float(i["makerFeeRate"]), "funding_interval_h": float(i["fundInterval"]),
                     "is_rwa": i.get("isRwa") == "YES", "asset_class": i.get("instrumentType")},
    }


def validate(snap: dict) -> dict:
    now_ms = int(snap.get("now_ms") or time.time() * 1000)
    purpose = snap.get("purpose", "execution")
    venue = str(snap.get("execution_venue", "")).lower()
    instrument = snap.get("instrument")
    errors, warnings, checked = [], [], []
    if purpose not in MAX_AGE_S:
        errors.append(f"unknown purpose {purpose!r}")
        purpose = "execution"
    if not instrument or not venue:
        errors.append("instrument and execution_venue are required")
    quotes = snap.get("quotes") or []
    if not quotes:
        return {"status": "INSUFFICIENT_DATA", "errors": errors + ["no quotes supplied"], "warnings": [], "quotes": []}

    usable = []
    for q in quotes:
        rec = {"provider": q.get("provider"), "exchange": q.get("exchange"), "instrument": q.get("instrument"),
               "timestamp_ms": q.get("timestamp_ms"), "timezone": q.get("timezone", "UTC")}
        problems = []
        if q.get("error"):
            problems.append(f"source error: {q['error']}")
        price, ts = num(q, "price"), num(q, "timestamp_ms")
        if price is None or price <= 0:
            problems.append("missing or non-positive price")
        if ts is None:
            problems.append("missing timestamp")
        else:
            age = (now_ms - ts) / 1000
            rec["latency_s"] = round(age, 3)
            if age < -MAX_FUTURE_SKEW_S:
                problems.append(f"timestamp {-age:.1f}s in the future (clock mismatch)")
            elif age > MAX_AGE_S[purpose]:
                problems.append(f"stale: {age:.1f}s old, limit {MAX_AGE_S[purpose]:.0f}s for {purpose}")
            rec["freshness"] = "fresh" if not problems else "stale_or_invalid"
        bid, ask = num(q, "bid"), num(q, "ask")
        if bid is not None and ask is not None:
            if bid <= 0 or ask <= 0 or bid > ask:
                problems.append("crossed or invalid bid/ask")
            else:
                rec["bid"], rec["ask"] = bid, ask
                rec["spread_bps"] = round((ask - bid) / ((ask + bid) / 2) * 1e4, 3)
                if rec["spread_bps"] > MAX_SPREAD_BPS:
                    warnings.append(f"{q.get('provider')}: wide spread {rec['spread_bps']} bps")
        rec["price"] = price
        rec["problems"] = problems
        rec["is_execution_venue"] = str(q.get("exchange", "")).lower() == venue
        # the exact contract must match: XAUUSD (CFD) is not XAUUSDT (perp); BTCUSD-PERP is not BTCUSDT
        rec["same_instrument"] = (q.get("instrument") == instrument and
                                  q.get("category", snap.get("category")) == snap.get("category"))
        checked.append(rec)
        if not problems:
            usable.append(rec)

    venue_quotes = [r for r in usable if r["is_execution_venue"] and r["same_instrument"]]
    mismatched = [r for r in checked if r["is_execution_venue"] and not r["same_instrument"]]
    if mismatched:
        errors.append("execution-venue quote is for a different contract: " +
                      ", ".join(f"{r['instrument']} != {instrument}" for r in mismatched))
    if purpose == "execution" and not venue_quotes:
        errors.append(f"no fresh quote for {instrument} from the execution venue ({venue}); "
                      "prices from other venues cannot be used for order levels")
    if purpose == "execution" and venue_quotes and "bid" not in venue_quotes[0]:
        errors.append("execution-venue quote lacks bid/ask")

    disagreement = None
    ref = venue_quotes[0]["price"] if venue_quotes else (usable[0]["price"] if usable else None)
    others = [r for r in usable if ref and r is not (venue_quotes[0] if venue_quotes else usable[0])]
    if ref and others:
        disagreement = max(abs(r["price"] - ref) / ref for r in others)
        if disagreement > MAX_SOURCE_DISAGREEMENT:
            errors.append(f"sources disagree by {disagreement:.2%} (limit {MAX_SOURCE_DISAGREEMENT:.1%}): investigate "
                          "(different contract, stale feed, USD vs USDT, or a venue-specific dislocation)")

    contract = snap.get("contract")
    if purpose == "execution":
        if not contract:
            errors.append("contract specification missing")
        else:
            if contract.get("symbol") != instrument:
                errors.append(f"contract spec is for {contract.get('symbol')}, not {instrument}")
            if contract.get("status") != "online":
                errors.append(f"instrument status is {contract.get('status')!r}")
            for k in ("price_tick", "qty_step", "min_qty", "min_notional", "taker_fee"):
                if num(contract, k) is None:
                    errors.append(f"contract field {k} missing")

    stale_only = bool(checked) and not usable
    status = ("STALE" if stale_only and not errors else
              "CONFLICT" if any("disagree" in e or "different contract" in e for e in errors) else
              "INSUFFICIENT_DATA" if errors else "VERIFIED")
    if stale_only:
        status = "STALE"
    return {"status": status, "purpose": purpose, "instrument": instrument, "execution_venue": venue,
            "checked_at_ms": now_ms, "reference_price": ref if status == "VERIFIED" else None,
            "max_source_disagreement": disagreement, "errors": errors, "warnings": warnings, "quotes": checked,
            "contract": contract if status == "VERIFIED" else None}


def main(argv: list[str]) -> int:
    if "--fetch-bitget" in argv:
        symbol = argv[argv.index("--fetch-bitget") + 1]
        try:
            snap = fetch_bitget(symbol)
        except Exception as exc:  # noqa: BLE001 - connector missing or blocked: fail closed
            return emit({"status": "INSUFFICIENT_DATA", "errors": [f"bitget fetch failed: {type(exc).__name__}: {exc}"],
                         "warnings": [], "quotes": []}, False)
    else:
        args = [a for a in argv[1:] if not a.startswith("--")]
        snap = json.loads(open(args[0]).read() if args else sys.stdin.read())
    res = validate(snap)
    return emit(res, res["status"] == "VERIFIED")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
