# Portfolio risk

Run `scripts/evaluate_portfolio_risk.py` before sizing. Its answer to "may we open anything new?" is binding.

## Provisional limits (paper configuration)
| Control | Limit |
|---|---|
| Planned risk per trade | 0.50% of trading equity |
| Total open planned risk | 1.50% |
| Daily loss circuit breaker | 1.50% |
| Weekly loss circuit breaker | 4.00% |
| Gross exposure | 3x equity, counting existing positions and pending entry orders |
| Margin mode | Isolated where the product supports it |

These are operating controls chosen for the paper phase, not proven optimal values. They can be tightened per request and are never loosened by the scripts.

## What blocks new entries
Kill switch; incomplete or stale account state (120 s for API data, 5 minutes for screenshots); equity cross-check off by more than 3%; a circuit breaker; an existing position or pending entry on the same symbol; exposure or open-risk caps; concentration.

## Correlation and concentration
Positions in the same asset group and direction are one bet. During broad sell-offs most crypto and equity perpetuals move together, so five shorts are one short with five fees. The script blocks a candidate when its group-and-direction cluster would hold more than two thirds of the open-risk cap. Also consider: spot holdings and long grid bots are long exposure; a short perp against them is a hedge that still has funding and liquidation risk of its own.

## Whole-account view
Include spot holdings, futures positions, pending orders, grid bots, collateral and cross-margin dependencies, borrowed liabilities, realised and unrealised P&L. Use the ledger model in `references/bitget-integration.md` to avoid double counting. Report unreadable ledgers as unreadable, never as zero.

## Existing positions without stops
The skill reports them as a warning and counts their notional. It does not place, move or cancel anything on them.
