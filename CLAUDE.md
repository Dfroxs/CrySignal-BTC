# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate
python3 run_bot.py              # run once
python3 run_bot.py --loop 60   # run every 60 minutes (loop mode)
python3 news_scraper.py        # scrape only (updates data/CSVs)
python3 core_analysis.py       # analyze only (reads existing CSVs)
python3 backtest.py            # replay 90 days of 1H OHLCV through the signal pipeline
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
| `SIGNAL_THRESHOLD` | 5.2 | Minimum weighted score to fire BUY/SELL |
| `TELEGRAM_BOT_TOKEN` | — | Enables Telegram alerts on BUY/SELL signals |
| `TELEGRAM_CHAT_ID` | — | Target chat for Telegram alerts |
| `DISCORD_WEBHOOK_URL` | — | Enables Discord alerts on BUY/SELL signals |
| `LOOP_INTERVAL` | 60 | Default loop cadence in minutes |

`config.py` also exports `HTTP_SESSION` (a shared `requests.Session` with retry/backoff), file path constants for all `data/` files, and `load_cache`/`save_cache` helpers for JSON caches.

## Architecture

Four-phase single-pass pipeline (no scheduler — run via cron or `--loop`):

**Phase 1 — `news_scraper.py`**
Fetches from FinancialJuice RSS, CoinGecko API, and ForexFactory XML macro calendar. Deduplicates by title, filters for crypto relevance, scores keyword sentiment, then writes:
- `data/crypto_news_sentiment.csv`
- `data/macro_events.csv`

Phase 1 failure is non-fatal in loop mode — analysis continues on stale CSVs.

**Phase 2 — `core_analysis.py`**
After fetching 1H OHLCV from Binance (ccxt), it spawns a `ThreadPoolExecutor(max_workers=5)` to fetch HTF trends (4H + 1D EMA200), funding rate, long/short ratio, DXY, S&P 500, stablecoin supply, BTC dominance, open interest, and Fear & Greed simultaneously. All HTTP calls use the shared `HTTP_SESSION` from `config.py`.

Signal scoring: seventeen weighted conditions → BUY/SELL fires when winning side ≥ `SIGNAL_THRESHOLD` (5.2). Display shows score out of `SIGNAL_MAX_SCORE` (18.0).

| # | Layer | Condition | Max weight |
|---|---|---|---|
| 1 | EMA 200 | Price above/below | 1.0 |
| 2 | RSI | Oversold/overbought/zone | 1.5 |
| 3 | MACD | Crossover / position | 1.5 |
| 4 | Volume | 1.3× avg confirms move | 1.0 |
| 5 | Bollinger Bands | At upper/lower band | 1.0 |
| 6 | HTF Alignment | 4H + 1D agree | 1.5 |
| 7 | RSI Divergence | Bullish/bearish divergence | 2.0 |
| 8 | OBV 5-candle slope | Accumulation/distribution | 0.75 |
| 9–11 | Market structure | Funding rate / L/S ratio / DXY | 1.0 / 0.75 / 0.5 |
| 12 | S&P 500 | Rising/falling (risk-on/off) | 1.0 |
| 13 | Stablecoin supply | Rising = dry powder / Falling = capital leaving | 0.75 |
| 14 | BTC Dominance | Rising = BTC inflow / Falling = altcoin rotation | 0.75 |
| 15 | Open Interest | Rising / falling with price | 0.5 |
| 16 | Futures basis | Premium / discount vs index | 0.5 |
| 17 | Stochastic RSI | Crossover in oversold/overbought | 1.0 |
| 18 | Support/Resistance | Bounce/rejection within 0.3% | 0.75 |
| 19 | VWAP | Price above/below 24h VWAP | 0.75 |

After signal generation, `integrate_news_with_signal()` applies the macro hold gate (HIGH impact USD event within 2h forces HOLD) and adjusts strength based on Fear & Greed.

**Phase 3 — `paper_trader.py`**
Checks all open paper positions in the SQLite DB against current price; closes any that hit TP or SL with WIN/LOSS outcome. BUY/SELL signals automatically open new paper positions.

**Phase 4 — `notifier.py`**
Sends Telegram and/or Discord alerts on BUY/SELL. Silent no-op if tokens are unset. Called from `run_bot.py` after analysis.

## Persistence

`signal_history.py` manages a SQLite database (`data/signal_history.db`) with two tables:
- `signals` — one row per signal fired (including HOLD), with all indicator values and outcome
- `paper_positions` — open/closed paper trades linked to signals

A legacy CSV (`data/signal_history.csv`) is still written as fallback on every signal. `migrate_from_csv()` runs on import to one-time-migrate old CSV rows into SQLite.

Several market structure data points are cached to JSON files in `data/` to avoid redundant API calls: stablecoin supply, BTC dominance, and open interest (`stablecoin_cache.json`, `btc_dom_cache.json`, `oi_cache.json`). The adaptive threshold state is persisted in `threshold_state.json`.

## Adaptive threshold

`get_adaptive_threshold()` in `core_analysis.py` raises `SIGNAL_THRESHOLD` automatically when too many signals have fired within the past 72 hours (`ADAPTIVE_WINDOW_HOURS`). If the rolling count exceeds `ADAPTIVE_MAX_SIGNALS` (8), the threshold scales up toward `THRESHOLD_MAX` (8.0). This prevents signal clusters during volatile periods.

## Key data flow detail

`core_analysis.get_combined_sentiment(fng=...)` reads the CSV written by Phase 1. The `fng` parameter accepts a pre-fetched Fear & Greed dict (fetched in the ThreadPoolExecutor to avoid a second round-trip). Macro hold check parses timestamps in format `MM-DD-YYYY H:MMam/pm` — events with unparseable times are silently skipped.

## Signal scoring — when adding new conditions

If you add conditions to `generate_signals()`, update `SIGNAL_MAX_SCORE` in `config.py` and consider whether `SIGNAL_THRESHOLD` (5.2) still represents ~30% of the new max. The threshold is intentionally above the original 4.0 to compensate for the larger condition set.

## Backtest limitations

`backtest.py` only tests technical conditions (EMA, RSI, MACD, volume, BB, HTF, divergence, OBV, StochRSI, S/R, VWAP). All market structure conditions (funding, L/S, DXY, S&P 500, stablecoin, BTC.D, OI) score as NEUTRAL during backtest — no paid historical API exists for these. The HTF trend is computed by resampling the 1H data in-process.
