# Position sizing

Run `scripts/calculate_position_size.py`; do not size by hand.

## The calculation
1. Risk budget = verified trading equity x risk per trade (0.50% provisional).
2. Loss per unit if stopped = (entry fill - stop fill) + entry fee + stop fee + expected funding cost, where fills include slippage (stops slip 1.5x).
3. Quantity = risk budget / loss per unit, then reduced to fit: remaining open-risk room (1.50% cap), gross-exposure room (3x including the owner's existing positions and pending entries), contract maximum, and available margin.
4. Round DOWN to the contract quantity step. Below the minimum order, the answer is "no trade", never "round up".

## Leverage: two different numbers
- **Effective leverage** = position notional / equity. This drives profit and loss.
- **Leverage setting** = the exchange dial. It only decides how much margin is locked and where liquidation sits.

The setting is derived after sizing: the largest value, capped at 20x, that keeps the estimated liquidation price at least two stop-distances from entry, using isolated margin. A user-requested setting is honoured only if it is lower (safer). Raising the dial never raises the position.

Worked example (verified by `tests/test_position_sizing.py`): equity 10,000 USDT, long BTCUSDT, entry 60,000, stop 59,400, taker 0.06%, slippage 0.02%. Loss per unit = (60,012 - 59,382.18) + (60,012 + 59,382.18) x 0.0006 = 701.4565. Quantity = 50 / 701.4565 = 0.0712 after rounding down. Notional 4,272 USDT, effective leverage 0.43x, setting 20x, margin 213.60, planned loss 49.94 USDT.

## Equity source
Sizing from verified account state (connector or fresh screenshots) is the only basis for an executable plan. When the user only states a number, size from it with `"equity_source": "user_stated"` so they get the figure they asked for, clearly marked indicative. Never size from old screenshots or remembered balances.

## Trailing exits
With `"exit_mode": "trail"` there are no TP1/TP2 and no fixed reward/risk. The plan reports the trailing rule and `reference_3r_price` only as a distance marker.

## Never
- Average down, add to a loser, or use martingale sizing.
- Widen a stop after entry.
- Size from an unverified or stale equity figure.
- Treat the daily and weekly circuit breakers as targets to trade up to.

## Requests for more risk
The limits are provisional paper controls and can be tightened in the request; the scripts will not loosen them. If the owner wants a higher risk fraction, show the drawdown table for the strategy's measured losing streak first (for TRX: 24 consecutive losses is in the record; at 1% that is about -23%, at 3% about -55%).
