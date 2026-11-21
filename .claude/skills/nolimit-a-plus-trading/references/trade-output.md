# Trade output

Use this block for every answer, in the user's language, with numbers copied from script output. Keep explanations to a few lines after the block.

```
STATUS:
PAIR:
DIRECTION:
STRATEGY:
SETUP SCORE:            (ranking, not a win probability)
VALIDATION STATUS:

ENTRY:
STOP LOSS:
LEVERAGE:               (setting, margin mode; effective leverage)
POSITION SIZE:
TAKE PROFIT 1:
TAKE PROFIT 2:

MAXIMUM PLANNED LOSS:
NET REWARD/RISK:
ESTIMATED EXECUTION COST:
DATA TIMESTAMP:
SETUP EXPIRY:
INVALIDATION:

FINAL DECISION:  TRADE CANDIDATE / WAIT / NO TRADE / INSUFFICIENT DATA
```

## Rules
- STATUS is one of: PAPER CANDIDATE, REFERENCE LEVELS ONLY, REJECTED, DATA UNAVAILABLE.
- If the data gate failed, write "-" for every price field. Do not show levels from stale or unverified data.
- If the decision is WAIT or NO TRADE and data is verified, levels may be shown with STATUS "REFERENCE LEVELS ONLY", and POSITION SIZE is shown only when account state is verified.
- Trailing strategies: write the trailing rule in TAKE PROFIT 1, "none (trailing exit)" in TAKE PROFIT 2, and "n/a (trailing)" in NET REWARD/RISK. The sizing output field `reference_3r_price` may be quoted as a distance marker, labelled as such.
- User-stated equity (no connector, no screenshots): show POSITION SIZE, LEVERAGE and MAXIMUM PLANNED LOSS from the sizing script followed by "(indicative: equity not verified)". The decision is never TRADE CANDIDATE in this case.
- No equity at all: write "- (needs account equity)" in POSITION SIZE and MAXIMUM PLANNED LOSS; LEVERAGE may still show the setting, which depends only on the stop distance.
- SETUP EXPIRY: the close time of the bar after the signal bar (signal bar open + 2 bar lengths) for one-bar triggers.
- After the block, give at most: the reason for the decision, the main contradicting evidence, and what would change the decision.
- Never state or imply a guaranteed outcome, and never report an order as placed or filled.

See `examples/valid_trade.json` and `examples/rejected_trade.json` for inputs that produce each outcome.
