# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
source venv/bin/activate
python3 run_bot.py              # one full cycle (spot + futures)
python3 run_bot.py --loop 60   # repeat every 60 minutes
python3 news_scraper.py        # Phase 1 only — update data/CSVs
python3 core_analysis.py       # Phase 2 only — reads existing CSVs
python3 backtest.py            # replay 90 days of 1H OHLCV (futures, default)
python3 backtest.py --mode spot # replay 90 days of 4H OHLCV (spot)
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
| `LOOP_INTERVAL` | 60 | Default loop cadence in minutes |

`config.py` also exports `HTTP_SESSION` (shared `requests.Session` with retry/backoff), all `data/` path constants, `load_cache`/`save_cache` JSON helpers, `EXECUTION_CONFIG` (fee/slippage %), `RISK_LIMITS` (drawdown/daily-loss circuit breakers), and `LEVERAGE_CONFIG` (conviction-based leverage multiplier params).

## Architecture

Four-phase single-pass pipeline per cycle:

**Phase 1 — `news_scraper.py`**
Fetches FinancialJuice RSS, CoinGecko API, and ForexFactory XML macro calendar. Deduplicates, scores keyword sentiment, writes `data/crypto_news_sentiment.csv` and `data/macro_events.csv`. Non-fatal — analysis continues on stale data if scrape fails.

**Phase 2 — `signals/` package** (10 modules; was monolithic `core_analysis.py`)

| Module | Purpose |
|---|---|
| `signals/indicators.py` | EMA, RSI, MACD, Bollinger, ATR, OBV, VWAP, StochRSI, ADX, divergence, S/R, regime |
| `signals/market_data.py` | Funding, L/S, DXY, S&P, stablecoin, BTC.D, OI, F&G, cache, adaptive threshold |
| `signals/htf.py` | Multi-timeframe trend + indicators (4H/1D for futures, 1D/1W for spot) |
| `signals/sentiment.py` | News CSV + Fear & Greed combined, macro event check |
| `signals/engine.py` | `generate_signals()` — 18 numbered conditions + `integrate_news_with_signal()` |
| `signals/sizing.py` | `calculate_position_size()`, `calculate_futures_position()` |
| `signals/ohlcv.py` | Fetch candles + compute all indicators |
| `signals/spot.py` | `analyze_spot_signal()` pipeline orchestrator |
| `signals/futures.py` | `analyze_futures_signal()` pipeline orchestrator |
| `signals/terminal.py` | `display_analysis()` + helpers |

`core_analysis.py` and `notifier.py` remain as thin re-export shims for backward compatibility.

*Spot (4H):* `analyze_spot_signal()` fetches 4H OHLCV (VWAP over 6 candles = 24H), then runs conditions via `generate_signals(..., mode='spot')`. HTF trend uses 1D + 1W EMA200 (`get_spot_htf_trend()`). Futures-only conditions (funding, L/S, OI, basis) are skipped. Threshold: `SPOT_THRESHOLD` (4.3), max score: `SPOT_MAX_SCORE` (17.25).

*Futures (1H):* `analyze_futures_signal()` fetches 1H OHLCV (VWAP over 24 candles), runs all conditions via `generate_signals(..., mode='futures')`. HTF trend uses 4H + 1D EMA200 (`get_htf_trend()`). Threshold: `SIGNAL_THRESHOLD` (5.2), max score: `SIGNAL_MAX_SCORE` (21.25).

Both use `ThreadPoolExecutor` to fetch market data in parallel. Spot skips funding rate, L/S ratio, and open interest fetches entirely.

**Condition table (†= futures-only):**

| # | Condition | Max weight |
|---|---|---|
| 1 | EMA 200 — price above/below | 1.0 |
| 2 | RSI — oversold/overbought/zone | 1.5 |
| 3 | MACD — crossover / position | 1.5 |
| 4 | Volume — 1.3× avg confirms move | 1.0 |
| 4b | Volume climax (Wyckoff) — vol >2× + narrow range | 0.75 |
| 5 | Bollinger Bands — at upper/lower/middle | 1.25 |
| 6 | HTF alignment — RSI + MACD + volume per timeframe | 2.0 |
| 7 | RSI divergence — pivot-based bullish/bearish | 2.0 |
| 8 | OBV 5-candle slope — accumulation/distribution | 0.75 |
| 9† | Funding rate — negative = shorts dominant | 0.5–1.0 |
| 9b† | L/S ratio — shorts crowded | 0.75 |
| 9c | DXY — falling = weak USD | 0.5 |
| 10 | S&P 500 — rising = risk-on | 1.0 |
| 11 | Stablecoin supply — rising = dry powder | 0.75 |
| 12 | BTC Dominance — rising = BTC inflow | 0.75 |
| 13† | Open Interest — rising = trend confirmation | 0.5 |
| 14† | Futures basis — premium vs index | 0.5 |
| 15 | Stochastic RSI — crossover in extreme zone | 1.0–1.25 |
| 16 | Support/Resistance — bounce/rejection within ATR | 0.75 |
| 17 | VWAP — price above/below rolling VWAP | 0.75 |
| 18 | ADX trend strength + DI crossover | 0.5–1.25 |

After scoring, `integrate_news_with_signal()` applies the macro hold gate (HIGH-impact USD event within 2h reduces score by −2.0 and forces HOLD if weak) and adjusts strength based on Fear & Greed.

**Phase 3 — `trading/` package**
- `trading/paper.py` — opens and closes paper positions per mode (`'spot'` or `'futures'`). Implements trailing stop + partial TP:
  - **TP1** (50%) hit → trailing stop moves to breakeven
  - **TP2** (50%, 2× TP1 distance) hit or trailing stop triggered → full close
  - P&L blended: `partial_pnl * 0.5 + remaining_pnl * 0.5`
  - MACRO_CLOSE outcome when all positions force-closed ahead of HIGH-impact event
- **Spot pyramiding:** on STRONG BUY with an open BUY position, opens a pyramid entry (max 3 total). Each level uses tighter SL (×0.8) and smaller size (×0.5), with 11 safety gates.
- Max positions: 3 spot (1 initial + 2 pyramid), 2 futures (1 LONG + 1 SHORT).

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

### Useful SQL queries

```sql
-- Win rate per mode
SELECT mode, outcome, COUNT(*) FROM paper_positions
WHERE outcome IS NOT NULL GROUP BY mode, outcome;

-- Signal frequency
SELECT mode, type, COUNT(*) FROM cycle_log GROUP BY mode, type;

-- Threshold drift over time
SELECT timestamp, mode, threshold FROM cycle_log ORDER BY id DESC LIMIT 50;

-- Average score by outcome
SELECT p.outcome, AVG(c.strength)
FROM paper_positions p JOIN cycle_log c ON p.signal_id = c.id
WHERE p.outcome IS NOT NULL GROUP BY p.outcome;
```

## Adaptive threshold

Both pipelines use `_get_adaptive_threshold(base, t_min, t_max, state_file, env_var)` — a shared helper. If >8 signals fired in the past 72h, threshold rises by +0.5 (capped at max). If 0 signals in the window, it drops by −0.25 (floored at min). Setting the env var to a non-zero value disables adaptation.

- Futures: base 5.2, min 4.0, max 8.0, state `threshold_state.json`
- Spot: base 4.3, min 3.0, max 7.0, state `spot_threshold_state.json`

## Signal scoring — when adding new conditions

- If adding to **futures only**: wrap in `if mode == 'futures':` inside `signals/engine.py:generate_signals()`, do not change `SPOT_MAX_SCORE`.
- If adding to **both modes**: update both `SIGNAL_MAX_SCORE` and `SPOT_MAX_SCORE` in `config.py`, and verify that both thresholds still represent ~25–30% of their respective max scores.
- `generate_signals(df, htf, market_structure, sr, mode='futures', threshold_override=None)` — `threshold_override` is how `signals/spot.py` and `signals/futures.py` inject their per-mode adaptive threshold.

## Backtest limitations

`backtest.py` only tests technical conditions (EMA, RSI, MACD, volume, BB, HTF, divergence, OBV, StochRSI, ADX, S/R, VWAP). All market structure conditions (funding, L/S, DXY, S&P, stablecoin, BTC.D, OI, basis) score as NEUTRAL — no historical API exists for these. HTF trend is computed by resampling 1H data in-process. Results are therefore **conservative** compared to live trading.

## Changelog

Update `CHANGELOG.md` after every fix or feature change.
