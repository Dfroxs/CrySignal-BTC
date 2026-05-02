# AGENTS.md

## Quick start

```bash
source venv/bin/activate          # Python 3.14
pip install -r requirements.txt   # ccxt, numpy, pandas, requests
cp .env.example .env              # edit if you want Telegram alerts
python3 run_bot.py                # single run
python3 run_bot.py --loop 60     # run every 60 min
python3 news_scraper.py           # Phase 1 only
python3 core_analysis.py          # Phase 2 only (reads stale CSVs)
```

## Architecture

Two-phase pipeline. No tests. No linter/formatter config.

**Phase 1 — `news_scraper.py`**: Fetches FinancialJuice RSS, CoinTelegraph RSS, Decrypt RSS, CoinGecko trending, and ForexFactory XML macro calendar → writes `data/crypto_news_sentiment.csv` and `data/macro_events.csv`. Phase 1 failure is non-fatal in loop mode (Phase 2 proceeds on stale CSVs).

**Phase 2 — `core_analysis.py`**: Fetches 1H OHLCV from Binance (ccxt), then spawns `ThreadPoolExecutor(max_workers=7)` to fetch in parallel: 4H+1D HTF trend, funding rate, long/short ratio, DXY, S&P 500, stablecoin supply, and Fear & Greed. All HTTP calls use `requests.Session` with `urllib3.Retry(total=3, backoff_factor=0.5)`.

Signal scoring: 14 weighted float conditions summed to buy/sell scores. Signal fires when winning side ≥ `SIGNAL_THRESHOLD` (4.5). Displayed score is out of `SIGNAL_MAX_SCORE` (15.0).

**`notifier.py`**: Sends Telegram alert on BUY/SELL. Silent no-op if `TELEGRAM_BOT_TOKEN` is unset.

## Configuration (`config.py`)

All values overridable via environment variables. No other env vars exist beyond what's in the file — there is **no** `CRYPTOPANIC_AUTH_TOKEN` (despite what CLAUDE.md says; CryptoPanic is not integrated).

## Key gotchas

- **Macro hold gate**: `integrate_news_with_signal()` checks for HIGH-impact USD events within 2h → forces HOLD. Timestamps in `MM-DD-YYYY H:MMam/pm` format; unparseable times are silently skipped.
- **`get_combined_sentiment(fng=...)`** accepts a pre-fetched Fear & Greed dict to avoid a second HTTP round-trip. If omitted, it fetches its own.
- **Condition numbering in `generate_signals()`** is misleading: comments say #1–#14 but #11 is mislabeled (VWAP at the end is labeled #12, after #14). The order is: EMA200, RSI, MACD, Volume, BB, HTF, RSI Divergence, OBV, Market Structure (funding + L/S + DXY), S&P500, Stablecoin, Stochastic RSI, Support/Resistance, VWAP.
- **When adding conditions**: update `SIGNAL_MAX_SCORE` in `config.py` and verify `SIGNAL_THRESHOLD` (4.5) still represents ~30% of the new max.
