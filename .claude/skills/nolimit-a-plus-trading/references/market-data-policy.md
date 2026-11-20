# Market data policy

A price is evidence only if we know where it came from and how old it is. Wrong-contract and stale quotes are the two most common ways an analysis that looks right loses money.

## Source order
1. **Execution venue first.** Order levels come only from the venue where the order will be placed (Bitget, category `USDT-FUTURES`, exact symbol). `scripts/validate_market_data.py --fetch-bitget SYMBOL` uses the public v3 API and needs no key.
2. **Corroboration.** Other connectors (TradingView MCP, Crypto.com, CoinGecko, CoinGlass, others) are used to cross-check and for context, never for order levels.
3. **User-supplied quotes** (screenshots) are accepted for research; for execution they must show a time inside the freshness limit.

## Record for every data point
provider, exchange, exact instrument identifier, category, contract specification, timestamp and timezone, observed price, bid, ask, latency, freshness status, source errors. The validator output already has this shape; keep it with the report.

## Freshness limits
| Use | Maximum quote age |
|---|---|
| Building executable levels | 15 seconds |
| Research and context | 60 minutes |
| Candles | last closed bar must be the most recent one; forming bars are never used for signals |

Never call a quote "live" unless the validator returned `VERIFIED` in this turn.

## Instrument identity
The same asset trades as different contracts with different prices and rules:
- `XAUUSDT` (Bitget USDT perpetual) is not `XAUUSD` / `XAUUSD.s` (Bitget CFD account) and not spot gold.
- `BTCUSDT` perpetual is not `BTCUSD-PERP` on another venue, and not spot `BTCUSDT`.
- Equity perpetuals (`NVDAUSDT`, `isRwa=YES`) follow their own sessions, funding and gaps.
Compare the symbol, the category and the contract spec. A mismatch is an error, not a rounding issue.

## Disagreement
If two fresh sources differ by more than 0.5%, stop and find out why before producing any plan: different contract, USD versus USDT quote, a stale feed, or a real venue dislocation. Report what was found. Do not average.

## Failure handling
Timeouts, rate limits, HTTP errors and empty responses are recorded in `error` and make that source unusable. If the execution venue is unusable, the decision is INSUFFICIENT DATA.
