# Pre-registration — does the counter-trend veto earn its place? (H-CT)

**Written 2026-10-09, before the instrument exists and before any figure below has been
computed.** The price data was already downloaded for H-F. No experiment here has run
the engine on it, and no counter-trend signal has ever been simulated in this repo.

## Why

Futures has opened zero shorts in runs 1–3. All 965 futures cycles since 2026-08-30 had
the 1D trend BULLISH. `signals/engine.py`'s counter-trend veto rejects every SELL when
1D is BULLISH (and every BUY when 1D is BEARISH), so a short has been impossible by
construction. 47 cycles had a SELL score above threshold, and at least 39 of them died
on this veto. The veto has never been tested. The 2026-09-24 test of the three
anti-chase vetoes FAILED: no evidence they pay for themselves.

## Data (fixed now)

- Binance BTCUSDT perpetual 1h, `2026-10-09-hist-futures-ic-run/perp_1h.csv`
  (SHA-256 in that folder's `sha256.txt`), 2020-01-01 → 2026-08-29 23:00.
- HTF frames (4h and 1D, the futures pair) are **resampled from the 1h base** with
  `signals.htf.htf_indicator_series`, as `scripts/entry_ic.py` does. Six years of 1h
  give real EMA200s on both. An HTF bar is read only once it has closed (`backtest._htf_at`).
  Evaluation starts once the 1D EMA200 is real.
- Indicators mirror `fetch_ohlcv_df` (VWAP over 24 bars for 1h). Market structure is
  None (funding etc. NEUTRAL), as in every backtest here.
- Live futures scores spot BTC/USDT, and this uses the perp. The difference is the
  basis, a few basis points, and it is disclosed and not corrected.

## Arms (independent entries, identical exit)

Every entry is simulated through `backtest._simulate_forward` in futures mode, after
`trading.paper.apply_futures_exit_geometry`. That is the live exit as deployed: stop and
target ×2, trail 3.5×ATR, 72h cap, futures costs.

- **`ct_vetoed`**: candles where the engine with `gates_disabled=("counter_trend",)`
  fires, and the engine as configured returns HOLD. Veto gates only turn a signal into
  HOLD, so these are exactly the signals the counter-trend veto alone removes. The
  other four vetoes still apply to them.
- **`random_ct`**: count-matched random entries, the same number per direction, drawn
  from candles in the same window whose 1D trend opposes that direction. Levels come
  from the ATR formula (`exit_ic._signal_at`, mirrored for SELL). 20 seeds, averaged.
- **`kept`** (descriptive): the engine as configured.

**De-duplication, applied to the engine arms:** after an entry in a direction, further
entries in that direction are skipped until it exits, as `backtest`'s counterfactual
does. This stops one setup that re-fires hourly from being counted dozens of times.
Random entries are sparse and are not de-duplicated.

## Windows

- **Primary:** 2021-01-01 → 2024-12-31.
- **Confirmation:** 2025-01-01 → 2026-08-29.

## Hypotheses (SELL in a 1D-BULLISH trend is the question; BUY in a 1D-BEARISH trend is secondary)

**H-CT1, selection.** Does the engine pick counter-trend entries better than random
counter-trend entries?
- PASS if `mean(ct_vetoed) − mean(random_ct) > 0` **and** ct_vetoed's 90% bootstrap
  interval excludes `mean(random_ct)`.
- FAIL if `mean(ct_vetoed) ≤ mean(random_ct)`.
- INCONCLUSIVE otherwise.

**H-CT2, profit.** Are those entries profitable after costs?
- PASS if `mean(ct_vetoed) > 0` **and** its 90% interval lies above 0.
- FAIL if the interval lies entirely below 0.
- INCONCLUSIVE otherwise.

**Power guard:** n(ct_vetoed) < 100 in the primary or < 30 in the confirmation →
INCONCLUSIVE for that window. Bootstrap: 5,000 resamples, seed 2026.

A hypothesis passes only if it passes in **both** windows.

## Reported regardless, never criteria

- The same table for BUY-in-bear, `kept`, and random with-trend entries.
- How many `ct_vetoed` entries would also fail the Phase 3 gates (`regime_counter`,
  `trend_confluence`, via `backtest._failing_gates`). Removing the veto alone would not
  open them.
- Per-year means.

## What the outcomes license

- **H-CT1 PASS and H-CT2 PASS (SELL):** evidence to allow counter-trend shorts. That
  means lifting the veto for SELL **and** treating `regime_counter`/`trend_confluence`
  the same way, in run 4, with the gates' overlap reported.
- **H-CT1 PASS, H-CT2 not PASS:** the engine selects, but not enough to beat costs.
  The veto stays. Opening them anyway would be a data-yield design decision, recorded
  as such and not as this result.
- **H-CT1 FAIL:** the engine has no edge against the trend. The veto is justified as a
  filter on noise and stays.

## Discard

- The configured engine fires any SELL while 1D is BULLISH (or a BUY while 1D is
  BEARISH): the veto is not doing what the instrument assumes. Asserted.
- The 1D EMA200 is not real before 2021-01-01, which would leave the primary window
  short of its start.
