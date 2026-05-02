# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate
python3 run_bot.py              # run once
python3 run_bot.py --loop 60   # run every 60 minutes (loop mode)
python3 news_scraper.py        # scrape only (updates data/CSVs)
python3 core_analysis.py       # analyze only (reads existing CSVs)
```

The venv uses **Python 3.14**. To install/sync dependencies:
```bash
venv/bin/pip install -r requirements.txt
```

## Configuration

All tunable values are in `config.py` and can be overridden via environment variables. Copy `.env.example` to `.env` and load it before running. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `ACCOUNT_BALANCE` | 1000 | Spot account balance (USDT) |
| `FUTURES_BALANCE` | 500 | Futures sub-account balance (USDT) |
| `SIGNAL_THRESHOLD` | 4.5 | Minimum weighted score to fire BUY/SELL |
| `TELEGRAM_BOT_TOKEN` | — | Enables Telegram alerts on BUY/SELL signals |
| `TELEGRAM_CHAT_ID` | — | Target chat for Telegram alerts |
| `CRYPTOPANIC_AUTH_TOKEN` | — | Required for reliable CryptoPanic results |
| `LOOP_INTERVAL` | 60 | Default loop cadence in minutes |

## Architecture

Two-phase single-pass pipeline (no scheduler — run via cron or `--loop`):

**Phase 1 — `news_scraper.py`**
Fetches from FinancialJuice RSS, CoinGecko API, CryptoPanic API, and ForexFactory XML macro calendar. Deduplicates by title, filters for crypto relevance, scores keyword sentiment, then writes:
- `data/crypto_news_sentiment.csv`
- `data/macro_events.csv`

Phase 1 failure is non-fatal in loop mode — analysis continues on stale CSVs.

**Phase 2 — `core_analysis.py`**
After fetching 1H OHLCV from Binance (ccxt), it spawns a `ThreadPoolExecutor(max_workers=5)` to fetch HTF trends (4H + 1D EMA200), funding rate, long/short ratio, DXY, and Fear & Greed simultaneously. All HTTP calls use a shared `requests.Session` with `urllib3.Retry(total=3, backoff_factor=0.5)`.

Signal scoring: eleven weighted float conditions → BUY/SELL fires when winning side ≥ `SIGNAL_THRESHOLD` (4.5). Display shows score out of `SIGNAL_MAX_SCORE` (15.0).

| Layer | Condition | Max weight |
|---|---|---|
| EMA 200 | Price above/below | 1.0 |
| RSI | Oversold/overbought/zone | 1.5 |
| MACD | Crossover / position | 1.5 |
| Volume | 1.3× avg confirms move | 1.0 |
| Bollinger Bands | At upper/lower band | 1.0 |
| HTF Alignment | 4H + 1D agree | 1.5 |
| RSI Divergence | Bullish/bearish divergence | 2.0 |
| OBV 5-candle slope | Accumulation/distribution | 0.75 |
| Market structure | Funding rate + L/S + DXY | 0.5 / 0.75 / 0.5 |
| Stochastic RSI | Crossover in oversold/overbought | 1.0 |
| Support/Resistance | Bounce/rejection within 0.3% | 0.75 |

After signal generation, `integrate_news_with_signal()` applies the macro hold gate (HIGH impact USD event within 2h forces HOLD) and adjusts strength based on Fear & Greed.

**`notifier.py`** — Sends Telegram message on BUY/SELL. Silent no-op if `TELEGRAM_BOT_TOKEN` is unset. Called from `run_bot.py` after analysis.

## Key data flow detail

`core_analysis.get_combined_sentiment(fng=...)` reads the CSV written by Phase 1. The `fng` parameter accepts a pre-fetched Fear & Greed dict (fetched in the ThreadPoolExecutor to avoid a second round-trip). Macro hold check parses timestamps in format `MM-DD-YYYY H:MMam/pm` — events with unparseable times are silently skipped.

## Signal scoring — when adding new conditions

If you add conditions to `generate_signals()`, update `SIGNAL_MAX_SCORE` in `config.py` and consider whether `SIGNAL_THRESHOLD` (4.5) still represents ~30% of the new max. The threshold is intentionally a bit above the original 4.0 to compensate for the larger condition set.
