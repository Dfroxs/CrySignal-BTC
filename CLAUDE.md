# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate
python3 run_bot.py              # one full cycle (spot + futures)
python3 run_bot.py --loop 60   # repeat every 60 minutes
python3 news_scraper.py        # Phase 1 only — update data/CSVs
python3 core_analysis.py       # Phase 2 only — reads existing CSVs
python3 backtest.py            # replay 90 days of 1H OHLCV through the futures pipeline
```

The venv uses **Python 3.14**. To install/sync dependencies:
```bash
venv/bin/pip install -r requirements.txt
```

## Configuration

All tunable values are in `config.py`, overridable via environment variables. Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `ACCOUNT_BALANCE` | 1000 | Spot account balance (USDT) |
| `FUTURES_BALANCE` | 500 | Futures sub-account balance (USDT) |
| `SIGNAL_THRESHOLD` | 5.2 | Futures BUY/SELL minimum score |
| `SPOT_THRESHOLD` | 4.3 | Spot BUY/SELL minimum score |
| `TELEGRAM_BOT_TOKEN` | — | Enables Telegram alerts |
| `TELEGRAM_CHAT_ID` | — | Target chat for Telegram |
| `DISCORD_WEBHOOK_URL` | — | Enables Discord alerts |
| `LOOP_INTERVAL` | 60 | Default loop cadence in minutes |

`config.py` exports `HTTP_SESSION` (shared `requests.Session` with retry/backoff), all `data/` path constants, and `load_cache`/`save_cache` JSON helpers.

## Architecture

Four-phase single-pass pipeline per cycle:

**Phase 1 — `news_scraper.py`**
Fetches FinancialJuice RSS, CoinGecko API, and ForexFactory XML macro calendar. Deduplicates, scores keyword sentiment, writes `data/crypto_news_sentiment.csv` and `data/macro_events.csv`. Non-fatal — analysis continues on stale data if scrape fails.

**Phase 2 — `core_analysis.py`** (two independent analyses)

*Spot (4H):* `analyze_spot_signal()` fetches 4H OHLCV (VWAP over 6 candles = 24H), then runs 15 conditions via `generate_signals(..., mode='spot')`. HTF trend uses 1D + 1W EMA200 (`get_spot_htf_trend()`). Futures-only conditions are skipped. Threshold: `SPOT_THRESHOLD` (4.3), max score: `SPOT_MAX_SCORE` (14.25).

*Futures (1H):* `analyze_futures_signal()` fetches 1H OHLCV (VWAP over 24 candles), runs all 19 conditions via `generate_signals(..., mode='futures')`. HTF trend uses 4H + 1D EMA200 (`get_htf_trend()`). Threshold: `SIGNAL_THRESHOLD` (5.2), max score: `SIGNAL_MAX_SCORE` (18.0).

Both use `ThreadPoolExecutor` to fetch market data in parallel. Spot skips funding rate, L/S ratio, and open interest fetches entirely.

**Condition table — futures (all 19) / spot (conditions marked † are futures-only):**

| # | Condition | Max weight |
|---|---|---|
| 1 | EMA 200 — price above/below | 1.0 |
| 2 | RSI — oversold/overbought/zone | 1.5 |
| 3 | MACD — crossover / position | 1.5 |
| 4 | Volume — 1.3× avg confirms move | 1.0 |
| 5 | Bollinger Bands — at upper/lower | 1.0 |
| 6 | HTF alignment — both timeframes agree | 1.5 |
| 7 | RSI divergence — bullish/bearish | 2.0 |
| 8 | OBV 5-candle slope — accumulation/distribution | 0.75 |
| 9† | Funding rate — negative = shorts dominant | 0.5–1.0 |
| 10† | L/S ratio — shorts crowded | 0.75 |
| 11 | DXY — falling = weak USD | 0.5 |
| 12 | S&P 500 — rising = risk-on | 1.0 |
| 13 | Stablecoin supply — rising = dry powder | 0.75 |
| 14 | BTC Dominance — rising = BTC inflow | 0.75 |
| 15† | Open Interest — rising = trend confirmation | 0.5 |
| 16† | Futures basis — premium vs index | 0.5 |
| 17 | Stochastic RSI — crossover in extreme zone | 1.0–1.25 |
| 18 | Support/Resistance — bounce/rejection within 0.3% | 0.75 |
| 19 | VWAP — price above/below rolling VWAP | 0.75 |

After scoring, `integrate_news_with_signal()` applies the macro hold gate (HIGH-impact USD event within 2h forces HOLD) and adjusts strength based on Fear & Greed.

**Phase 3 — `paper_trader.py`**
Opens and closes paper positions per mode (`'spot'` or `'futures'`). Implements trailing stop + partial TP:
- **TP1** (50%) hit → trailing stop moves to breakeven
- **TP2** (50%, 2× TP1 distance) hit or trailing stop triggered → full close
- P&L blended: `partial_pnl * 0.5 + remaining_pnl * 0.5`

Max positions: 2 spot (`RISK_CONFIG["max_positions"]`), 2 futures (`FUTURES_CONFIG["max_positions"]`).

**Phase 4 — `notifier.py`**
`send_signal_alert(spot_signal, futures_signal)` sends one combined Telegram/Discord message if at least one signal is non-HOLD. Each section (SPOT and FUTURES) shows full analysis: price & trend, HTF alignment, technicals, market structure, sentiment + headlines, and signal reasons — matching terminal output. Telegram uses HTML parse mode.

## Persistence

`signal_history.py` — SQLite `data/signal_history.db`:
- `signals` table — one row per signal fired, with indicator snapshot and outcome
- `paper_positions` table — open/closed trades with `mode` column (`'spot'`/`'futures'`), trailing stop, TP1/TP2, partial close state

All queries that touch paper positions accept `mode=None` (all) or `mode='spot'`/`mode='futures'` for per-mode filtering: `get_open_positions(mode)`, `get_closed_pnl(mode)`.

Legacy CSV (`data/signal_history.csv`) is still written as fallback. `migrate_from_csv()` runs on import.

**Cache files in `data/`** (6-hour TTL, checked by `_cache_fresh()`):
- `stablecoin_cache.json`, `btc_dom_cache.json`, `oi_cache.json` — market structure data
- `threshold_state.json` — futures adaptive threshold rolling signal log
- `spot_threshold_state.json` — spot adaptive threshold rolling signal log

## Adaptive threshold

Both pipelines use `_get_adaptive_threshold(base, t_min, t_max, state_file, env_var)` — a shared helper. If >8 signals fired in the past 72h, threshold rises by +0.5 (capped at max). If 0 signals in the window, it drops by -0.25 (floored at min). Setting the env var to a non-zero value disables adaptation.

- Futures: base 5.2, min 4.0, max 8.0, state `threshold_state.json`
- Spot: base 4.3, min 3.0, max 7.0, state `spot_threshold_state.json`

## Signal scoring — when adding new conditions

- If adding to **futures only**: wrap in `if mode == 'futures':` inside `generate_signals()`, do not change `SPOT_MAX_SCORE`.
- If adding to **both modes**: update both `SIGNAL_MAX_SCORE` and `SPOT_MAX_SCORE` in `config.py`, and verify that both thresholds still represent ~30% of their respective max scores.
- `generate_signals(df, htf, market_structure, sr, mode='futures', threshold_override=None)` — `threshold_override` is how `analyze_spot_signal()` and `analyze_futures_signal()` inject their per-mode adaptive threshold.

## Backtest limitations

`backtest.py` only tests technical conditions (EMA, RSI, MACD, volume, BB, HTF, divergence, OBV, StochRSI, S/R, VWAP). All market structure conditions score as NEUTRAL — no historical API exists for these. HTF trend is computed by resampling 1H data in-process. Calls `generate_signals()` without a mode arg, which defaults to `'futures'` (backward-compatible).
