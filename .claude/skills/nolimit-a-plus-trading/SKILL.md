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
2. **Analyse structure.** Run `python scripts/analyze_market.py <SYMBOL> --style swing|day [--side long|short]`. It returns the regime per timeframe, live strategy triggers with entry and stop, funding, open interest, depth, and a `setup` object with the objective scores already filled in. Read `references/setup-validation.md` to interpret it and `references/derivatives-analysis.md` for the derivatives fields. Check scheduled events yourself with a sourced calendar and set `macro_event_risk`; leave it 0 when you could not check. If there is no live trigger for the direction the user wants, there is nothing to trade: the answer is NO TRADE, however strong the move looks.
3. **Check the evidence.** Look the strategy up in `assets/strategy_registry.json`. For a new idea or a re-test, run `scripts/backtest_strategy.py` and read `references/quantitative-research.md` before interpreting it.
4. **Size the position.** `python scripts/calculate_position_size.py request.json`, explained in `references/position-sizing.md`. Use the entry and stop from step 2, the contract block from step 1, `"exit_mode": "trail"` for trailing strategies, and the funding rate with `expected_hold_h` from `suggested_expected_hold_h`. If equity was only stated by the user, pass `"equity_source": "user_stated"`: the size is shown as indicative and the decision cannot be TRADE CANDIDATE.
5. **Check the account.** Read `references/portfolio-risk.md`; build the account JSON (with the candidate's notional and planned loss from step 4) and run `python scripts/evaluate_portfolio_risk.py account.json`. Set `source` to `bitget-api-readonly`, `screenshots`, or `user_stated`; copy the returned `account_state_status` (VERIFIED, USER_STATED or UNVERIFIED) into the setup. See `references/bitget-integration.md` for which Bitget view feeds which field.
6. **Challenge it.** Answer the adversarial questions in `references/setup-validation.md` in writing. Any unanswered question is a veto.
7. **Score and decide.** `python scripts/evaluate_setup.py setup.json`. Report with the template in `references/trade-output.md`, copying numbers from script output without rounding them differently.

For execution rules after a paper entry (stop handling, partial exits, reconciliation, kill switch), read `references/execution-protocol.md`.

## Final decisions

| Decision | When |
|---|---|
| TRADE CANDIDATE | Score >= 90, all seven gates pass, strategy is `VALIDATED`. Paper execution only. |
| WAIT | Promising but unproven strategy, score below 90, conflicting signals, or trigger not reached. |
| NO TRADE | Rejected or unknown strategy (including no evidence for this asset group or direction), no live trigger, failed liquidity or portfolio gate, circuit breaker, kill switch. |
| INSUFFICIENT DATA | Stale, missing or conflicting market data; or everything else passes but the account state is not verified. |

Requests like "short it now at market" or "use 50x" are answered with the same workflow. An entry without an objective trigger maps to no tested strategy, so it is NO TRADE; a requested leverage is honoured only when it is safer than the computed one.

A score is a ranking of research quality. It is not a probability of winning, so never translate 95/100 into "95% chance".

## Output

Always answer with the compact block from `references/trade-output.md`, in the user's language. When the decision is not TRADE CANDIDATE, levels may be shown only as "reference levels, not a trade instruction", and only if the data gate passed.

## When things are missing

- No network or connector for Bitget: ask for the four screenshots listed in `references/bitget-integration.md`; market levels then need a user-supplied quote with a visible time, and stay INSUFFICIENT DATA for execution if older than the freshness limit.
- No derivatives or macro source: state it and score those components 0 rather than guessing.
- Sources disagree: investigate first (different contract, USD vs USDT, stale feed). Do not average them.
