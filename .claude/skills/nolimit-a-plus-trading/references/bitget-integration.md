# Bitget integration

## Access model
- **Public market data:** no key. `https://api.bitget.com/api/v3/market/{tickers,instruments,candles,history-candles,current-fund-rate,open-interest,orderbook}` with `category=USDT-FUTURES`.
- **Account read:** a separate read-only key (no trade, no withdraw, IP-bound) used by a local process or an authorised connector. Never ask the user to paste a key, secret or passphrase into chat; if they do, tell them to revoke it.
- **Demo trading:** separate demo key and header `paptrading: 1`.
- **Live trading:** not available through this skill.

The companion repository `nolimit-aplus` implements the signed client, audit and paper engine; this skill works without it using screenshots.

## Account views and double counting
Each amount belongs to exactly one ledger:

| Ledger | Bitget source | Contains |
|---|---|---|
| unified | `GET /api/v3/account/assets` -> `accountEquity` | Spot holdings, futures margin and unrealised P&L of the unified account |
| funding | `GET /api/v3/account/funding-assets` | Funding account balances (price them with spot tickers) |
| bots | `GET /api/v2/account/bot-assets?accountType=futures|spot` | Capital inside grid bots |
| cfd | `GET /api/v3/cfd/account/fund-detail` | TradFi CFD account |

Total = unified + funding + bots + cfd. Do not add spot value, futures balance or unrealised P&L again; they are inside `unified`. The app's "Overview" is the total itself and is used only as a cross-check (3% tolerance).

Trading equity for sizing is the equity of the account where the order will be placed (normally `unified`), not the grand total.

## Without API access: four screenshots
Ask for these, in USDT, uncropped, with the phone clock visible, less than 5 minutes old:
1. Assets -> Unified Trading Account (equity, available USDT).
2. Futures -> Positions, scrolled to the end.
3. Futures -> Open Orders, including TP/SL and trigger orders.
4. P&L for today and the last 7 days.
An empty tab is evidence; a missing screenshot is unknown. After entry, ask for the position detail showing fill price and attached stop.

## Contract facts that differ by product
- Positions: size is field `total`; one-way mode still reports `posSide`.
- Hold mode is account-wide (`one_way_mode` or `hedge_mode`); never change it for the user.
- Equity, metal and energy perpetuals are ordinary `USDT-FUTURES` symbols flagged `isRwa=YES`, with their own funding intervals and session gaps.
- CFDs use a separate `/api/v3/cfd/*` family, different symbols, and have no client order id.
- Grid bot detail needs a known bot id; there is no list endpoint.

## Never touch
Existing positions, open orders and bots that this workflow did not create. A new idea on a symbol the owner already holds is rejected, because in one-way mode a new order would change their position.
