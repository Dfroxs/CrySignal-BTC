# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate
python3 run_bot.py              # one full cycle (spot + futures)
python3 run_bot.py --loop 60   # repeat every 60 minutes
python3 news_scraper.py        # Phase 1 only — update data/CSVs
python3 backtest.py            # replay 90 days of 1H OHLCV (futures, default)
python3 backtest.py --mode spot # replay 90 days of 4H OHLCV (spot)
```

The venv uses **Python 3.14**. To install/sync dependencies:
```bash
venv/bin/pip install -r requirements.txt
```

## Configuration

All tunable values are in `config.py`, overridable via environment variables. Copy `.env.example` to `.env` — it lists every supported variable.

## Architecture

Four-phase single-pass pipeline per cycle:

**Phase 1 — `news_scraper.py`**
Fetches FinancialJuice RSS, CoinGecko API, and ForexFactory XML macro calendar. Deduplicates, scores keyword sentiment, writes `data/crypto_news_sentiment.csv` and `data/macro_events.csv`. Non-fatal — analysis continues on stale data if scrape fails.

**Phase 2 — `signals/` package**
Computes indicators, scores every numbered condition in `signals/engine.py:generate_signals()`, and applies the news/macro overlay.

*Spot (4H):* `analyze_spot_signal()` fetches 4H OHLCV (VWAP over 6 candles = 24H), then runs conditions via `generate_signals(..., mode='spot')`. HTF trend uses 1D + 1W EMA200 (`get_spot_htf_trend()`). Futures-only conditions (funding, L/S, OI, basis) are skipped. Threshold: `SPOT_THRESHOLD` (4.3), max score: `SPOT_MAX_SCORE`.

*Futures (1H):* `analyze_futures_signal()` fetches 1H OHLCV (VWAP over 24 candles), runs all conditions via `generate_signals(..., mode='futures')`. HTF trend uses 4H + 1D EMA200 (`get_htf_trend()`). Threshold: `SIGNAL_THRESHOLD` (5.2), max score: `SIGNAL_MAX_SCORE`.

Both use `ThreadPoolExecutor` to fetch market data in parallel. Spot skips funding rate, L/S ratio, and open interest fetches entirely.

After scoring, `integrate_news_with_signal()` applies the macro hold gate (HIGH-impact USD event within 2h reduces score by −2.0 and forces HOLD if weak) and adjusts strength based on Fear & Greed.

**Phase 3 — `trading/` package**
- `trading/paper.py` — opens and closes paper positions per mode (`'spot'` or `'futures'`). Implements trailing stop + partial TP:
  - **TP1** (50%) hit → trailing stop moves to breakeven
  - **TP2** (50%, 2× TP1 distance) hit or trailing stop triggered → full close
  - P&L blended: `partial_pnl * 0.5 + remaining_pnl * 0.5`
  - MACRO_CLOSE outcome when all positions force-closed ahead of HIGH-impact event
- **Spot pyramiding:** on STRONG BUY with an open BUY position, opens a pyramid entry (max 3 total). Each level uses tighter SL (×0.8) and smaller size (×0.5), with 11 safety gates.
- Max positions: 3 spot (1 initial + 2 pyramid). Futures holds **one** position
  at a time — an opposite signal closes and flips, a same-direction one is skipped.

**Phase 4 — `notifier/` package**
- `notifier/common.py` — shared helpers + `send_signal_alert()` dispatcher
- `notifier/telegram.py` — Telegram formatters (compact card, consolidated, close) + sender
- `send_signal_alert(spot_signal, futures_signal)` sends one combined Telegram message. Each section shows full analysis: price & trend, market structure, top headlines, all-time performance, position sizing (SPOT + FUTURES), and NOTE verdict. HTML parse mode.

## Persistence

`trading/history.py` — SQLite `data/signal_history.db` with three tables:
- `signals` — one row per signal fired, with indicator snapshot and outcome
- `paper_positions` — open/closed trades with `mode` column (`'spot'`/`'futures'`), trailing stop, TP1/TP2, partial close state
- `cycle_log` — every cycle including HOLDs; 35+ fields: scores, all technicals, market structure, HTF indicators (JSON), sentiment, reasons, open position count

All queries that touch paper positions accept `mode=None` (all) or `mode='spot'`/`mode='futures'` for per-mode filtering: `get_open_positions(mode)`, `get_closed_pnl(mode)`.

Legacy CSV (`data/signal_history.csv`) is still written as fallback. `migrate_from_csv()` runs on import.

**Cache files in `data/`** (6-hour TTL, checked by `_cache_fresh()`):
- `stablecoin_cache.json`, `btc_dom_cache.json`, `oi_cache.json` — market structure data
- `threshold_state.json` — futures adaptive threshold rolling signal log
- `spot_threshold_state.json` — spot adaptive threshold rolling signal log

## Signal scoring — when adding new conditions

- If adding to **futures only**: wrap in `if mode == 'futures':` inside `signals/engine.py:generate_signals()`, do not change `SPOT_MAX_SCORE`.
- If adding to **both modes**: update both `SIGNAL_MAX_SCORE` and `SPOT_MAX_SCORE` in `config.py`, and verify that both thresholds still represent ~25–30% of their respective max scores.
- `generate_signals(df, htf, market_structure, sr, mode='futures', threshold_override=None)` — `threshold_override` is how `signals/spot.py` and `signals/futures.py` inject their per-mode adaptive threshold.

## Backtest limitations

`backtest.py` only tests technical conditions (EMA, RSI, MACD, volume, BB, HTF, divergence, OBV, StochRSI, ADX, S/R, VWAP). All market structure conditions (funding, L/S, DXY, S&P, stablecoin, BTC.D, OI, basis) score as NEUTRAL — no historical API exists for these. Results are therefore **conservative** compared to live trading.

HTF comes from the real 1D/1W (spot) or 4H/1D (futures) series fetched from the exchange, using the same `htf_indicator_series()` the live path uses — not from resampling the base timeframe, which could not hold enough bars for an EMA200. The backtest reads only HTF bars that had already **closed** at the candle being evaluated, so it lags live by at most one HTF bar rather than leaking the rest of a forming bar backwards.

`_fetch_ohlcv_paged()` pages around the exchange's 1000-bar cap on a single `fetch_ohlcv()` call — without it a request for 2360 hourly candles silently returned 1000.

## Changelog

Update `CHANGELOG.md` after every fix or feature change.
