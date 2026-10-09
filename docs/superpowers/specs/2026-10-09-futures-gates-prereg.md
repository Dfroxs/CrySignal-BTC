# Pre-registration — do the futures entry gates earn their place under the new exit?

**Written before any test-window figure exists.** Instrument `scripts/gate_ic.py`
committed at `46db2e8`. It has not been run on 2022-01 → 2025-10.

## Origin, and why that data is excluded

The corrected backtest for 2025-10-09 → 2026-10-09 (live-equivalent indicator windows, new
futures exit) flipped the gate picture:

| | per trade | n |
|---|---:|---:|
| all futures signals, ungated | +0.031pp | 78 |
| kept by the gates | −0.104pp | 8 |
| blocked by the gates | +0.046pp | 70 |

`fakeout_first` blocked the most (53, +0.058pp each). That year is also the year the exit
candidate was chosen on, so it is excluded here.

## Data and protocol

- **BTC/USDT futures 1h, 2022-01-01 → 2025-10-08**, fetched as `backtest.py` fetches.
  The research instruments, including `trail_ic`, have read these candles before. None of
  them tested the gates under the new exit, so this is an a-priori test on read data,
  not a holdout.
- Code at `46db2e8`: live-equivalent windows (499 base / 250 HTF bars, VWAP 24),
  futures exit trail 3.5×ATR with stop/target ×2, re-entry anchor 168h.
- One `run_backtest(mode="futures", counterfactual=True)` run. The kept trades are the
  ones it took. Blocked signals are simulated as shadows, one at a time per direction.

## Statistic, per gate

Gates under test: `fakeout_first`, `regime_counter`, `trend_confluence`, `psy_sl_first`,
`sr_first`. Confidence and re-entry are excluded, because they have their own tests.

For gate g: **only_g** = resolved shadow trades whose *only* failing gate was g. These are
exactly what removing g alone would admit. **kept** = resolved trades taken.

- **FAIL** if mean(only_g) ≥ mean(kept) → **remove g** from futures first entries.
- **PASS** if mean(kept) − mean(only_g) > 0 **and** its 90% bootstrap CI (5,000
  resamples) lies entirely above 0 → keep.
- **INCONCLUSIVE** otherwise, or if only_g has fewer than 20 trades → keep.

The burden is on the gate, as in `2026-09-24-gates-holdout-prereg.md`. A gate that cannot
show it blocks worse trades than it lets through is not kept on faith. A FAIL needs only
the direction. A PASS needs the interval.

**Descriptive only:** the kept vs all-blocked means for the whole stack, and each
gate's "any" counts.

## What a FAIL licenses

Disabling that gate for **futures first entries and flips** in live (`run_bot.py`)
and in `backtest._failing_gates`, via a config list. Spot is untouched. This changes the
trading path, so under the owner's ruling it ships mid-run, recorded in PAPER_RUN.md, and
any hypothesis that depends on which futures positions open (H-X) counts only from that
restart.

It does **not** license removing a gate that is INCONCLUSIVE, or tuning a gate's
threshold.

## Discard

- `run_backtest` raising, or kept n < 20.
- Any protocol difference from the above.
