"""NO LIMIT A+++ trading intelligence for an existing Bitget account.

Hard design rules (enforced in code, covered by tests):
  * No verified data  -> no trade.
  * No validated edge -> no A+++.
  * No protective risk control -> no execution.
  * Production order placement is not implemented anywhere in this package.
"""

__version__ = "0.1.0"

# Compile-time constant, intentionally not configurable from env or CLI.
LIVE_TRADING_ENABLED = False
