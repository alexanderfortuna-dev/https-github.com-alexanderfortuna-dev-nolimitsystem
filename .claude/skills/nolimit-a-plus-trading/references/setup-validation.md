# Setup validation

## 1. Multi-timeframe read
`scripts/analyze_market.py` produces this read from closed bars. Bitget history on new contracts can be short; when a timeframe lacks 200 bars the script says so and falls back to a weaker test. The weekly frame is built from daily bars and skipped below 60 weeks.

| Style | Regime | Structure | Trigger | Timing |
|---|---|---|---|---|
| Swing | 1W | 1D | 4H | 1H |
| Day | 1D | 4H | 1H / 15M | 5M |

Work from the highest timeframe down and write one line per timeframe:
- **Regime:** trending up, trending down or ranging (EMA50 vs EMA200 and price vs EMA200 is the default objective test).
- **Trend strength:** slope and distance from the mean in ATR units; note exhaustion (more than 3 ATR from EMA20).
- **Structure:** last confirmed swing high/low, structural breaks, nearest support and resistance.
- **Volatility:** ATR(14) as a percentage of price, and whether it is expanding or contracting versus ATR(100).
- **Volume:** confirmation or absence on the breakout bar.
- **False-breakout risk:** prior failed breaks at the level, thin session, event inside the next bars.

Timeframes that disagree are recorded under `conflicting_signals`; they cap the decision at WAIT.

## 2. Objective entry and invalidation
A setup is valid only if a second person could place the same order from the description:
- an exact trigger price and order type (stop-entry, limit on retest),
- an expiry (bar count or time),
- a stop at the price where the idea is wrong, outside normal noise (between 0.5 and 3 ATR, never wider than 25% of price),
- targets or a trailing rule defined before entry.
"Looks strong", "feels ready" and levels chosen after the fact do not qualify.

## 3. Strategy families
| Family | Objective definition | Registry id |
|---|---|---|
| Trend continuation / breakout | 55-bar breakout with trend, 2 ATR stop, 3 ATR trailing exit | TRX |
| Breakout after contraction | 20-bar range break with ATR(14) < ATR(100) | DBO |
| Pullback in trend | EMA20 reclaim bar in an EMA50/200 trend | TPB |
| Counter-trend reversal after a 3 ATR extension (capitulation or blow-off) | Reversal bar after the extreme, stop beyond the 3-bar swing | CAP (rejected) |
| Breakout and retest, failed-breakout reversal, other mean reversion | Not yet specified and tested | none: research only |

Exact triggers, stops and exits for each id are in the `exit` block of `assets/strategy_registry.json`, and `scripts/analyze_market.py` computes them live. Evidence is scoped: a result on crypto does not cover gold, and a long-side edge does not cover shorts (`asset_groups`, `direction_status`). Check the status in `assets/strategy_registry.json`. Families without a registry entry can be analysed and backtested, never recommended.

## 4. Macro and event risk
Check, with source and time for each: central-bank decisions, CPI/PCE, employment data, Treasury yields and USD strength, exchange announcements for the instrument (delisting, funding or margin changes), and asset-specific catalysts (earnings for equity perpetuals, unlocks or upgrades for tokens). A high-impact event inside the expected holding window lowers `macro_event_risk` and usually means WAIT. State correlations as observations; do not assert causes you cannot support.

## 5. Scoring (100 points)
| Component | Max | Full marks when |
|---|---|---|
| Market structure | 15 | All analysed timeframes agree |
| Entry quality | 15 | Objective trigger, not extended, clean invalidation |
| Statistical edge | 20 | Registry status VALIDATED with positive OOS expectancy (capped at 8 for RESEARCH_CANDIDATE, 0 otherwise) |
| Liquidity | 10 | 24h turnover above 100M USD, spread under 2 bps |
| Volatility | 5 | Stop outside noise, no volatility shock |
| Derivatives evidence | 10 | OI, funding and positioning corroborate (0 when no source) |
| Macro event risk | 10 | No high-impact event in the holding window (0 when unchecked) |
| Execution cost | 5 | Fees + spread + slippage under 5% of the stop distance |
| Risk/reward | 10 | Net reward/risk of 2 or more after costs |

Score 90+ is a preliminary research qualification only.

## 6. Seven gates for an executable candidate
1. Data quality verified this turn.
2. Entry and invalidation are objective.
3. Relevant out-of-sample evidence exists.
4. Net expectancy is positive after costs.
5. Liquidity is sufficient for the size.
6. Portfolio risk is acceptable.
7. The independent challenge below passes.

## 7. Independent challenger
Answer each in one or two sentences, as if arguing against the trade. An unanswered question or an answer that undermines the thesis is a veto.
- What evidence contradicts the thesis?
- Is the entry late (how many ATR from the breakout or the mean)?
- Is there a better instrument for the same idea?
- Is liquidity poor for this size or session?
- Are costs underestimated (funding over the hold, stop slippage, gaps)?
- Is the same exposure already held (correlated positions, bots, spot)?
- Does this strategy have a measured edge in this direction and regime?
- What exactly invalidates the trade, and is the stop there?
