# Hima Gold Research Bot

Research-only signal system for Exness MT5 XAUUSD, USOIL and ETHUSD. It does **not** place trades. It does not promise 90% win rate; it publishes confluence scores, not probability claims.

## Architecture

- `mt5_bridge.py` runs on a Windows PC/Windows VPS with Exness MT5 installed and logged in. It exports closed M5/M15/H1/H4 candles and current bid/ask from the broker terminal.
- `server.py` runs as a Render web service. It validates the bridge token, applies the multi-timeframe strategy, checks the Trading Economics calendar, deduplicates signals in SQLite and posts Telegram alerts.
- `engine.py` calculates EMA, RSI, MACD, ATR, ADX and volume confirmation. Both BUY and SELL are supported.
- `backtest.py` tests a simplified M15-only ruleset on broker-exported CSV. It is deliberately labelled separately from the live multi-timeframe strategy.

## 1. Telegram setup

1. Create a Telegram bot with BotFather.
2. Add it as an administrator to your private channel with permission to post.
3. In Render environment variables set `BOT_TOKEN` and `CHANNEL_ID` (for a channel, usually a `-100...` ID).

## 2. Render deployment

1. Push this folder to a GitHub repository.
2. In Render choose **New + > Blueprint** and connect the repository; Render reads `render.yaml`.
3. Set `BOT_TOKEN`, `CHANNEL_ID`, and `TE_API_KEY` in the Render service environment.
4. Keep `REQUIRE_NEWS_CHECK=true` for fail-closed news filtering. Without a valid Trading Economics API key, signals are blocked rather than pretending news was checked.
5. Copy the generated `BRIDGE_TOKEN` value. Keep it secret.

The blueprint uses a persistent disk for the SQLite deduplication database. A paid Render service is required for the persistent disk. Do not run multiple Gunicorn workers: SQLite deduplication is designed for one service instance.

## 3. Exness MT5 bridge (Windows only)

1. Install MetaTrader 5 from Exness and log in to the account. Keep the terminal open and connected.
2. Install 64-bit Python 3.12 and run:
   `py -m pip install -r requirements-bridge.txt`
3. Copy `.env.bridge.example` to `.env` and fill in the Render URL and `BRIDGE_TOKEN`.
4. Verify the exact symbol names in Exness Market Watch. Some accounts use suffixes such as `.m`; set the exact names in `.env`.
5. In PowerShell set variables (or use your preferred secret manager), then run `py mt5_bridge.py`.

Example PowerShell:

```powershell
$env:SIGNAL_SERVER_URL="https://YOUR-RENDER-SERVICE.onrender.com"
$env:BRIDGE_TOKEN="YOUR_RENDER_BRIDGE_TOKEN"
$env:EXNESS_GOLD_SYMBOL="XAUUSD"
$env:EXNESS_OIL_SYMBOL="USOIL"
$env:EXNESS_ETH_SYMBOL="ETHUSD"
py mt5_bridge.py
```

The official MetaTrader Python integration communicates with a locally installed MT5 terminal and its package is distributed as Windows wheels. Therefore the bridge belongs on Windows; Render hosts the API/research service.

## 4. News filter

The service uses the Trading Economics calendar API. Add your own API key as `TE_API_KEY`. The default blocks signals when the calendar cannot be checked or a high-impact US event is within 30 minutes. Review the provider's plan, rate limits and calendar coverage before relying on it.

## 5. Backtesting

Export M15 history from the same Exness symbol in MT5 as CSV with columns:
`time,open,high,low,close,tick_volume`

Then run on a machine with pandas installed:

```bash
python backtest.py XAUUSD_M15.csv --sl-atr 1.5 --rr 1.5
```

This script is a simple baseline test, not a full tick-level execution simulator and not the same as the live four-timeframe strategy. It assumes next-bar-open entries and conservatively counts a bar touching both SL and TP as a loss. Add realistic spread, commission, slippage and out-of-sample periods before assessing performance.

## 6. Important operating checks

- First run on a demo account and a private test Telegram channel.
- Confirm symbol names, quote digits, session hours, spread and candle history.
- Confirm calendar API access; news filter intentionally fails closed by default.
- No auto-trading or order placement is included.
- A `8/10` confluence score is not an 80% probability. Measure actual win rate, expectancy, drawdown and sample size from forward-tested signals.
- Never share the Telegram bot token, Exness password or bridge token in chat or source control.
