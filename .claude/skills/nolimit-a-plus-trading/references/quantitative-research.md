# Quantitative research

Read this before running or interpreting `scripts/backtest_strategy.py`.

## What counts as evidence
A strategy is `VALIDATED` only when a reproducible, fixed-rule test passes all of these on a pool of instruments from one asset group:

| Gate | Threshold |
|---|---|
| Out-of-sample trades (pool) | at least 100 |
| OOS net expectancy | at least +0.10R after all costs |
| OOS profit factor | at least 1.25 |
| 95% lower bound of expectancy (weekly block bootstrap) | above 0 |
| In-sample expectancy | above 0 (no sign flip) |
| Cost stress (fees x1.25, slippage x2) | still above 0 |
| Stability | at least 60% of quarters positive |
| Multiple testing | Holm-adjusted p below 0.05 across every hypothesis tested |
| Forward evidence | at least 20 forward trades with positive expectancy |

Thresholds are provisional operating values, not proven optima. Changing them requires a written reason, not a result that happens to fail.

## Bias controls
- **Look-ahead:** indicators at bar i use data through bar i only; signals use closed bars; the trailing stop in force during a bar was set at the previous close.
- **Data leakage:** strategy rules are written down before the test. The 60/40 chronological split is fixed.
- **Overfitting:** no parameter search. A new variant is a new hypothesis and is counted in the multiple-testing correction.
- **Repeated looks:** forward evidence only counts from a recorded freeze timestamp.
- **Survivorship:** universes built from today's liquid contracts overstate results; say so in every report.

## Fill and cost model
Stop-market entries fill at the trigger or the worse open, plus slippage. A stop inside the entry bar counts as a loss. Stop and target in the same bar counts as the stop. Stops gap to the worse open with 1.5x slippage. Targets need a trade-through. Taker fee on entries and stops, maker on targets. Funding is always charged as a cost in the standalone script.

## Reading the script output
Report these and nothing invented: trades evaluated, net expectancy (R), win rate with its 95% interval, profit factor, maximum drawdown, average win and loss, walk-forward folds, cost sensitivity, regime split (long/short, low/high volatility), Monte Carlo drawdown at the configured risk, and the listed limitations. A single-instrument run rarely has enough trades to prove anything: describe it as "consistent with" or "not consistent with" the pooled evidence.

## Current evidence (see `assets/strategy_registry.json`)
- TPB and DBO: rejected, zero or negative after costs.
- TRX: research candidate on crypto 4H. +0.16R over 1,424 trades (2021-2026), interval +0.02 to +0.30, edge concentrated on the long side (+0.28R long, +0.04R short), longest losing streak 24. Not yet through the multiple-testing and forward gates.
- TRX-A (alignment filter): rejected; aligned shorts lost 0.13R per trade.
No strategy is VALIDATED today, so the skill cannot currently produce a TRADE CANDIDATE.
