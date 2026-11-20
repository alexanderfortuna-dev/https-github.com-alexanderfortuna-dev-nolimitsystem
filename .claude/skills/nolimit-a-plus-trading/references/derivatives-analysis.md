# Derivatives intelligence

Derivatives data describes positioning and cost. It corroborates or weakens a setup; it never creates one.

## Inputs and where they come from
| Input | Source | Notes |
|---|---|---|
| Funding rate, next funding, interval | Bitget `GET /api/v3/market/current-fund-rate`, history via `/history-fund-rate` (about 90 days) | Intervals differ by contract (4h or 8h); annualise before comparing |
| Open interest | Bitget `GET /api/v3/market/open-interest` | Compare change with price change |
| Order book depth | Bitget `GET /api/v3/market/orderbook` (limit up to 1000) | Size must be small versus depth inside the stop distance |
| Taker buy/sell, liquidations, long/short ratios, liquidation maps | CoinGlass or similar, when a connector is authorised | Third-party estimates |
| Spot versus futures activity | Bitget SPOT and USDT-FUTURES tickers | Basis and relative turnover |

When a source is unavailable, write "not available" and score `derivatives_evidence` 0. Do not infer positioning from price alone.

## Interpretation rules
- **Funding** is a carrying cost first. Positive funding means longs pay shorts. Convert to an annual rate and to an expected cost over the holding period, and pass it to the sizing script. Extreme funding signals crowding, not direction.
- **Open interest** rising with price shows new positions, not who is right. Use it with funding and taker flow, never alone, as direction evidence.
- **Liquidation heatmaps** are model estimates of where leveraged positions might be closed. Treat clusters as zones of possible volatility, not targets or guarantees.
- **Crowding:** one-sided funding plus rising OI plus a one-sided long/short ratio raises squeeze risk against the crowd. It lowers the score of a trade in the crowded direction.
- **Liquidity:** if the planned size exceeds about 1% of visible depth inside the stop distance, reduce size or skip.

## Equity and commodity perpetuals
Funding on these contracts is often structurally high (tens of percent annualised) and they gap around the underlying session. Include funding over the planned hold in net reward/risk; many long swing ideas fail this alone.
