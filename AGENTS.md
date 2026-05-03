# AGENTS.md

## Quick start

```bash
source venv/bin/activate          # Python 3.14
pip install -r requirements.txt   # ccxt, numpy, pandas, requests
cp .env.example .env              # edit if you want Telegram / Discord alerts
python3 run_bot.py                # single run (scrape + analyze + paper trade)
python3 run_bot.py --loop 60     # run every 60 min
python3 news_scraper.py           # Phase 1 only
python3 core_analysis.py          # Phase 2 only (reads stale CSVs)
python3 backtest.py               # replay 90d through signal pipeline
```

## Architecture

Three-phase pipeline per cycle. No tests. No linter/formatter config.

**Phase 1 — `news_scraper.py`**: Fetches FinancialJuice RSS, CoinTelegraph RSS, Decrypt RSS, CoinGecko trending, Reddit (r/Bitcoin + r/CryptoCurrency), and ForexFactory XML macro calendar → writes `data/crypto_news_sentiment.csv` and `data/macro_events.csv`. Phase 1 failure is non-fatal (Phase 2 proceeds on stale CSVs).

**Phase 2 — `core_analysis.py`**: Fetches 1H OHLCV from Binance (ccxt), then spawns `ThreadPoolExecutor(max_workers=10)` to fetch in parallel: HTF trend, funding rate, long/short ratio, DXY, S&P 500, stablecoin supply, BTC dominance, open interest, liquidation clusters, and Fear & Greed. All HTTP calls use a shared `requests.Session` from `config.py` with `urllib3.Retry(total=3, backoff_factor=0.5)`.

17 weighted float conditions summed to buy/sell scores. Signal fires when winning side ≥ `SIGNAL_THRESHOLD` (5.2, adaptive). Displayed score out of `SIGNAL_MAX_SCORE` (18.0).

**Phase 3 — `run_bot.py`**: Opens paper trade if signal fires, then `paper_trader.py` checks all open positions against current price → auto-closes WIN/LOSS when TP/SL hit.

**Supporting modules**:
- `config.py` — all constants, shared `HTTP_SESSION`, env vars, `load_cache()` / `save_cache()` helpers
- `notifier.py` — Telegram + Discord webhook alerts (silent no-op if tokens unset)
- `signal_history.py` — SQLite-backed signal + paper position storage (auto-migrates from CSV)
- `paper_trader.py` — auto-close paper positions, P&L tracking
- `backtest.py` — replay 90d of 1H data through `generate_signals()` (technical-only, no lie data)

## Configuration (`config.py`)

All values overridable via environment variables. There is **no** `CRYPTOPANIC_AUTH_TOKEN` (CryptoPanic is not integrated).

| Variable | Default | Purpose |
|---|---|---|
| `SIGNAL_THRESHOLD` | 5.2 | Minimum score to fire (adaptive override via env) |
| `SIGNAL_MAX_SCORE` | 18.0 | Display ceiling |
| `TELEGRAM_BOT_TOKEN` | — | Telegram alerts |
| `DISCORD_WEBHOOK_URL` | — | Discord alerts |
| `LOOP_INTERVAL` | 60 | Loop cadence (minutes) |

## Signal conditions (#1–#17)

| # | Condition | Max Weight |
|---|---|---|
| 1 | EMA 200 trend | 1.00 |
| 2 | RSI | 1.50 |
| 3 | MACD crossover / position | 1.50 |
| 4 | Volume confirmation (≥1.3x avg) | 1.00 |
| 5 | Bollinger Bands | 1.00 |
| 6 | HTF Alignment (4H + 1D) | 1.50 |
| 7 | RSI Divergence | 2.00 |
| 8 | OBV 5-candle slope (threshold ≥0.1%) | 0.75 |
| 9 | Funding rate + L/S ratio + DXY | 0.5/0.75/0.5 |
| 10 | S&P 500 trend | 1.00 |
| 11 | Stablecoin supply | 0.75 |
| 12 | BTC Dominance | 0.75 |
| 13 | Open Interest | 0.50 |
| 14 | Futures basis (mark vs index spread) | 0.50 |
| 15 | Stochastic RSI (reduced when RSI extreme) | 0.15–1.00 |
| 16 | Support/Resistance proximity (≤0.3%) | 0.75 |
| 17 | VWAP | 0.75 |

## Key gotchas

- **Macro hold gate**: `integrate_news_with_signal()` checks for HIGH-impact USD events within 2h → forces HOLD. Timestamps in `MM-DD-YYYY H:MMam/pm` format; unparseable times silently skipped.
- **`get_combined_sentiment(fng=...)`** accepts a pre-fetched F&G dict to avoid a second round-trip.
- **OBV threshold**: Only scores when `|slope| / OBV[-5] ≥ 0.001` (flat OBV no longer contributes noise).
- **TP capped at S/R**: When resistance/support is within 85% of ATR-based TP, TP is clamped there (minimum 1.0 R:R).
- **Adaptive threshold**: If >8 signals in 72h, threshold raises +0.5 (max 8.0). If 0 signals, lowers -0.25 (min 4.0). Override with `SIGNAL_THRESHOLD` env var (any non-zero value disables adaptation).
- **Backtesting** only tests 11 technical conditions (no funding, L/S, DXY, etc. history available).
- **Liquidation heatmap** removed — Binance `/fapi/v1/allForceOrders` deprecated. Replaced with futures basis signal (mark vs index spread) extracted from existing `premiumIndex` call.
- **When adding conditions**: update `SIGNAL_MAX_SCORE` and `SIGNAL_THRESHOLD` in `config.py`.
