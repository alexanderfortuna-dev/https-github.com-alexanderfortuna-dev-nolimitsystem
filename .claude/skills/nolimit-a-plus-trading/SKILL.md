---
name: nolimit-a-plus-trading
description: Systematic trade qualification, position sizing and risk control for a real Bitget account (crypto, metal, commodity and equity perpetuals; day and swing trading). Use this skill whenever the user asks for a trade setup, entry, stop loss, take profit, leverage, position size, "A+++ setup", a market scan, the best pairs today, a backtest or validation of a strategy, a portfolio or margin risk check, or sends Bitget account screenshots, even if they only say "long or short BTC?", "cari setup", "berapa leverage", or "hajar". It verifies data freshness, scores the setup, checks out-of-sample evidence and portfolio limits, and returns a compact ENTRY / STOP LOSS / LEVERAGE / POSITION SIZE / TP1 / TP2 report or an explicit NO TRADE. Paper and analysis only; it never places live orders.
---

# NO LIMIT A+++ trading

Purpose: sustainable compounding on a real account. The most valuable output of this skill is often
"NO TRADE", because one avoided bad trade is worth more than one extra average trade.

Three rules decide everything else:

1. **No verified data, no trade.** Price levels are only produced from fresh quotes of the venue where the order will be placed.
2. **No validated edge, no A+++.** A good-looking chart is a hypothesis. Only strategies marked `VALIDATED` in `assets/strategy_registry.json` can produce a TRADE CANDIDATE.
3. **No risk control, no execution.** Size comes from the stop distance and account risk. The leverage setting never overrides it.

This skill never submits live orders, never asks for API secrets, and never modifies the owner's existing positions or bots. If a step cannot be completed, stop and say which step failed. Do not fill gaps with assumptions.

## Workflow

Follow the steps in order. Each script prints JSON and exits non-zero when its gate fails; a failed gate ends the workflow with the matching final decision.

1. **Get market data from the execution venue.** Read `references/market-data-policy.md`, then run
   `python scripts/validate_market_data.py --fetch-bitget <SYMBOL>` (or pass a snapshot JSON built from connector output). Status must be `VERIFIED`. Otherwise the decision is `INSUFFICIENT DATA`.
2. **Analyse structure.** Follow `references/setup-validation.md` for the multi-timeframe read, the entry trigger and the invalidation level. Add derivatives context with `references/derivatives-analysis.md` and scheduled-event risk from the macro section of `references/setup-validation.md` when sources are available; say so when they are not.
3. **Check the evidence.** Look the strategy up in `assets/strategy_registry.json`. For a new idea or a re-test, run `scripts/backtest_strategy.py` and read `references/quantitative-research.md` before interpreting it.
4. **Check the account.** Read `references/portfolio-risk.md`; build the account JSON from an authorised read-only connection or from the owner's screenshots, then run `python scripts/evaluate_portfolio_risk.py account.json`. See `references/bitget-integration.md` for which Bitget view feeds which field.
5. **Size the position.** `python scripts/calculate_position_size.py request.json`, explained in `references/position-sizing.md`.
6. **Challenge it.** Answer the adversarial questions in `references/setup-validation.md` in writing. Any unanswered question is a veto.
7. **Score and decide.** `python scripts/evaluate_setup.py setup.json`. Report with the template in `references/trade-output.md`, copying numbers from script output without rounding them differently.

For execution rules after a paper entry (stop handling, partial exits, reconciliation, kill switch), read `references/execution-protocol.md`.

## Final decisions

| Decision | When |
|---|---|
| TRADE CANDIDATE | Score >= 90, all seven gates pass, strategy is `VALIDATED`. Paper execution only. |
| WAIT | Promising but unproven strategy, score below 90, conflicting signals, or trigger not reached. |
| NO TRADE | Rejected or unknown strategy, failed liquidity or portfolio gate, circuit breaker, kill switch. |
| INSUFFICIENT DATA | Stale, missing or conflicting data; unverified account state; a required source is unavailable. |

A score is a ranking of research quality. It is not a probability of winning, so never translate 95/100 into "95% chance".

## Output

Always answer with the compact block from `references/trade-output.md`, in the user's language. When the decision is not TRADE CANDIDATE, levels may be shown only as "reference levels, not a trade instruction", and only if the data gate passed.

## When things are missing

- No network or connector for Bitget: ask for the four screenshots listed in `references/bitget-integration.md`; market levels then need a user-supplied quote with a visible time, and stay INSUFFICIENT DATA for execution if older than the freshness limit.
- No derivatives or macro source: state it and score those components 0 rather than guessing.
- Sources disagree: investigate first (different contract, USD vs USDT, stale feed). Do not average them.
