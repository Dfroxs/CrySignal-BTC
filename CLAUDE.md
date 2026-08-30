# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## A validation run is live — parameters are frozen

`v2.9.0` runs unattended on a VPS (`~/playground/CrySignal-BTC`, systemd unit
`spotsignal`), collecting the out-of-sample sample the project has never had.
`data/paper_run_manifest.json` on that host pins the commit and every parameter
it is testing.

**Do not change a threshold, weight, gate or risk parameter while it runs.** An
edit mid-run voids the sample — which is exactly how the previous attempt at
this ended up unusable. Land changes on `develop`; the server tracks `main` and
is updated deliberately:

```bash
cd ~/playground/CrySignal-BTC && git pull && sudo systemctl restart spotsignal
```

`deploy/setup.sh` provisions a fresh host end to end. Its step 0 refuses to
proceed unless Binance answers 200 — see the silent-failure note below for why
that check is not optional.

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

⚠️ Setting `SIGNAL_THRESHOLD` or `SPOT_THRESHOLD` in `.env` **switches the
adaptive controller off entirely** — `_get_adaptive_threshold()` returns the
override and never runs its frequency or win-rate logic. Both lines ship
commented out for that reason.

⚠️ **When Binance is unreachable the bot does not fail — it degrades.** Funding,
L/S, open interest, basis and taker ratio each fall back to NEUTRAL on their own,
so the system keeps running as a quieter, weaker version of itself: 7.5 of the
26.5-point futures ceiling, gone, with nothing in the signal to say so. The spot
mirror in `signals/market_data.py` covers spot reads only; futures endpoints have
no fallback. Detect it after the fact with:

```sql
SELECT COUNT(*) FROM cycle_log WHERE mode='futures' AND funding_rate = 0;
```

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

`generate_signals(df, htf=None, market_structure=None, sr=None, mode='futures', threshold_override=None, disabled=None)`

- `threshold_override` is how `signals/spot.py` and `signals/futures.py` inject their per-mode adaptive threshold.
- `disabled` selects the active condition set: `None` applies `config.DISABLED_CONDITIONS`, an empty collection scores everything, and a list ablates exactly those names. Ablation rolls the accumulators back at the condition's checkpoint, so it cannot drift from the real scoring path — but flags a condition sets for later blocks (`_rsi_os` and friends) are *not* unset.

**Adding a condition means adding a row to `config.CONDITION_MAX`**, keyed by the name its `_mark()` checkpoint emits, with its BUY-side ceiling for `(spot, futures)`. Use `0.00` for the mode that skips it. `SIGNAL_MAX_SCORE` and `SPOT_MAX_SCORE` are summed from that table over the active set — do not hand-edit them.

Thresholds are derived as a **fraction of that ceiling**, not absolute numbers, so pruning a condition lowers the bar with it. The fractions in `config.py` reproduce the historical 5.2 / 4.3 / 4.0 / 8.0 / 3.0 / 7.0 exactly at the pre-pruning maxima of 26.50 and 22.50 — that is 19.6% and 19.1% of max, not the ~25–30% an earlier revision of this file claimed.

Every condition also emits `signal['_contributions'][name] = (buy_delta, sell_delta)` via a read-only checkpoint. `scripts/condition_ic.py` correlates those against forward returns; `backtest.py --disable` ablates them.

### Threshold layering — do not re-floor in the engine

Three things adjust the bar, and each owns a different layer:

1. `market_data._get_adaptive_threshold()` moves the **base** on signal
   frequency and win rate, and clamps it with `max(base - step, t_min)`. The
   per-mode minimum lives here and nowhere else.
2. `generate_signals()` adds the **session and regime bumps** on top, in full.
3. Only `_ABS_MIN_THRESHOLD` (0.5) remains as a floor — below that a threshold
   is an off switch, not a bar.

Re-applying the per-mode minimum after step 2 looks like a safety fix and is
not: once the controller has walked the base down to `THRESHOLD_MIN`,
`max(4.0 − 0.25 − 0.25, 4.0)` discards both negative bumps at exactly the moment
a lower bar is the controller's intent, leaving only the `+0.5` Asia bump. This
was shipped and reverted within one release; `test_engine_does_not_reapply_the_mode_minimum`
guards it.

## Backtest limitations

`backtest.py` only tests technical conditions (EMA, RSI, MACD, volume, BB, HTF, divergence, OBV, StochRSI, ADX, S/R, VWAP). All market structure conditions (funding, L/S, DXY, S&P, stablecoin, BTC.D, OI, basis) score as NEUTRAL — no historical API exists for these. Results are therefore **conservative** compared to live trading.

HTF comes from the real 1D/1W (spot) or 4H/1D (futures) series fetched from the exchange, using the same `htf_indicator_series()` the live path uses — not from resampling the base timeframe, which could not hold enough bars for an EMA200. The backtest reads only HTF bars that had already **closed** at the candle being evaluated, so it lags live by at most one HTF bar rather than leaking the rest of a forming bar backwards.

`_fetch_ohlcv_paged()` pages around the exchange's 1000-bar cap on a single `fetch_ohlcv()` call — without it a request for 2360 hourly candles silently returned 1000. `_fetch_ohlcv_range()` pages forward through an explicit span so one period can be tested against another.

An HTF fetch that fails degrades to `htf=None` with a warning rather than aborting the run — `scripts/backtest_offline.py` relies on that, since it patches `fetch_ohlcv_df` and the HTF loader deliberately bypasses it.

### Backtest flags

```
--start / --end YYYY-MM-DD   explicit window, so 2024 can be tested against 2025
--walk-forward N             split the window into N sequential periods, parameters fixed
--gates                      also simulate every signal the entry gates rejected
--costs                      re-price every trade at other execution-cost levels
--disable a,b                ablate conditions on top of config.DISABLED_CONDITIONS
--all-conditions             ignore DISABLED_CONDITIONS for this run
```

`--gates` reports per-trade P&L on both sides. Comparing totals across unequal trade counts cannot show whether the gates *select* or merely *thin*; only the per-trade figures can.

### A threshold sweep does not measure selectivity

Two things make threshold comparisons in this harness misleading, and both are
structural rather than statistical:

1. **Lowering the threshold replaces trades rather than adding them.**
   `open_until` blocks a same-direction signal while a position is open, so a
   lower bar fires an earlier signal whose position then swallows the window the
   higher bar's trade would have used. Measured on spot 2024: the losing trade
   at $43,242 that the 4.3 threshold takes does not exist at 2.8, where two
   different winners appear near the same price instead. Each threshold samples
   a **different sequence**, so results are non-monotonic by construction.

2. **`OPEN` rows inflate the apparent sample.** A position still alive at
   `max_hold` is recorded at 0.00% and excluded from every statistic. Spot 2024
   reports 3 signals at threshold 4.3 but closes **2**; 8 signals at 2.8 but
   closes **5**.

Together these mean no threshold in this repository has ever been calibrated on
more than a handful of closed trades drawn from mutually exclusive sequences —
including the configured 5.2 / 4.3. Treat a sweep as a description of what
happened, never as a basis for picking a value.

## scripts/

- `condition_ic.py` — scores each condition's contribution against forward
  returns over thousands of candles. The full system fires ~12–15 times a year
  and can never be validated on its own trade count; its components are
  evaluated every candle. `--start/--end` makes one period testable against
  another, `--horizons` sweeps holding periods from a single pass.
- `start_paper_run.py` — writes `data/paper_run_manifest.json` on the host that
  will run it. A manifest describes one run on one machine, so it is generated
  there and never committed.
- `backtest_offline.py` — deterministic replay from `data/btc_*_90d.csv`
  (not in the repo). It patches `fetch_ohlcv_df`; the HTF loader bypasses that
  patch by design and degrades to `htf=None`.
- `loss_forensics.py` — post-mortem on individual losing trades.

Run the heavy ones on a workstation, not the VPS: `backtest.py` and
`condition_ic.py` hold frames of thousands of rows and will exhaust 1 GB. They
need nothing from the server — both replay market data fetched from the
exchange. Only `analyze.py` reads the run's database:

```bash
scp <host>:~/playground/CrySignal-BTC/data/signal_history.db data/server.db
./venv/bin/python analyze.py --db data/server.db
```

`--db` exists so a pulled copy can be analysed without overwriting the local
database, which is the only copy of whatever it holds. Every report names its
source file and the span it covers, because two hosts otherwise produce reports
that are indistinguishable.

## Changelog

Update `CHANGELOG.md` after every fix or feature change.
