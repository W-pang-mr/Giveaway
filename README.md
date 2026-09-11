# TON Wallet Watch Bot

A small Telegram bot that watches public TON wallet addresses and sends notifications for incoming and outgoing transfers.

## Features

- /start explains the bot and shows the controls.
- Set one public TON address per Telegram user.
- Toggle notifications on or off.
- Notify incoming TON/Jetton transfers as deposits.
- Notify outgoing TON/Jetton transfers as withdrawals.
- Ignore historical transactions when an address is first added.
- SQLite persistence and duplicate-event protection.
- No seed phrase or private key is ever requested.

## Required secret

Set BOT_TOKEN in Replit Secrets. Never put it in this repository.

Optional environment variables:

- TONAPI_TOKEN: optional TonAPI bearer token for higher API limits.
- DB_PATH: SQLite path, default wallet_watch.sqlite3.
- POLL_SECONDS: polling interval, default 20 seconds and minimum 10.
- TONAPI_BASE: default https://tonapi.io.

## Important behavior

A deposit means an incoming transfer to the watched address. A withdrawal means an outgoing transfer from it. The bot reads public blockchain data only; it does not sign, send, or control funds.

Run with: python bot.py
