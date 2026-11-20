# Execution protocol (paper and demo only)

Live order submission is outside this skill. The protocol below is what the paper engine in the companion repository enforces, and what to tell the owner to do when they place a trade by hand.

## Order lifecycle
1. **Trigger entry.** The plan is armed and fires only when a fresh quote crosses the trigger. If price is already more than 0.25R beyond the trigger, skip: do not chase.
2. **Stop with the entry.** The entry order carries its stop loss (mark-price trigger). A position without a confirmed stop is closed immediately.
3. **Exits are reduce-only.** Target strategies: 50% at TP1, then the stop moves to entry, remainder at TP2. Trailing strategies: no fixed target; the stop is raised at each bar close and never lowered.
4. **Expiry.** An untriggered plan is cancelled at its expiry.
5. **Time stop.** Close after the strategy's maximum holding period.

## Duplicate prevention and reconciliation
- One trade per signal; a deterministic client order id per order.
- After a timeout or unknown response, query by client order id. Never resend blindly.
- Every cycle compare positions with the book. A size the book cannot explain engages the kill switch.
- Fills are booked once, by execution id. Never report a fill that was not returned by the venue.

## Kill switch
Engage on: position mismatch, a failed or rejected emergency close, a circuit breaker, loss of the data feed during an open plan, or the owner's request. It cancels armed entries and blocks new ones. Flattening positions opened by the system is optional and explicit. Positions the system did not open are never touched.

## Audit log
For every decision keep: the data validation output, the account snapshot, the sizing output, the score and gates, the challenger's answers, the final decision, and all order and fill events with timestamps.

## Manual execution checklist for the owner
1. Margin mode isolated; leverage setting from the report.
2. Entry order type and price exactly as reported; stop loss attached in the same order.
3. Screenshot the position after the fill.
4. Move the stop only in the profit direction, on bar close, as the plan states.
5. No second order on the same symbol.
