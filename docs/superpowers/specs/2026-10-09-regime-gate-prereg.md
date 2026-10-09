# Pre-registration — does the spot regime gate earn its place? (H-RG)

**Written 2026-10-09, before the instrument exists and before any figure below has been
computed.**

## Why

Since 12:34 UTC spot runs without the anti-chase vetoes. The first spot BUY to clear the
bar (13:01, 5.25 vs 4.05, NORMAL) was blocked in Phase 3 by `regime_bearish`: BUY refused
while the regime is TRENDING/VOLATILE with ADX trend_dir BEARISH (`run_bot._is_bearish_regime`,
named `regime_counter` in `backtest._failing_gates`). This gate has never been tested on
spot. The futures gates test (`2026-10-09-futures-gates-results.md`) was INCONCLUSIVE,
because the gates overlap and few signals are blocked by one gate alone.

## Data (fixed now)

- The sibling project's hash-verified OKX 4h cache, loaded by `scripts/entry_ic.py`'s
  `load_symbol`. Those are its 10 `TUNING` symbols (BTC, ETH, SOL, BNB, OKB, ICP,
  SUSHI, NEAR, UNI, DOGE). Nakhoda's locked market-holdout symbols are not read.
- HTF (1D, 1W) resampled from the 4h base (`entry_ic.build_htf`, strict warmup).
- The engine runs **as deployed at `316e0dc`**: `gates_disabled=None` applies
  `config.VETOES_DISABLED` (spot anti-chase off) and the R:R float fix.
- **Primary window:** from each symbol's warmup to 2025-08-30. **Confirmation:**
  2025-08-30 → 2026-08-30 (Nakhoda's time holdout for these symbols, used before only by
  the 09-24 entry test, which did not apply Phase 3 gates).

## Arms

Every engine BUY with confidence ≥ NORMAL. `confidence_first` precedes this gate live, so
WEAK signals could never reach it. Each is run through `backtest._failing_gates(sig,
"spot", window, None)`. The re-entry gate needs trade history and is not simulated.

- **`regime_only`**: BUYs whose failing gates are exactly `["regime_counter"]`. These
  are what removing the gate would open.
- **`kept`**: BUYs that fail no gate.
- **`random_regime`**: count-matched random entries (per window, pooled across
  symbols, drawn per symbol in proportion to `regime_only`'s count) from candles whose
  regime is TRENDING/VOLATILE with trend_dir BEARISH. ATR levels via
  `exit_ic._signal_at`. 20 seeds.

All arms go through `backtest._simulate_forward` in spot mode (the live spot exit and
costs). Engine arms are de-duplicated per symbol: no new entry while that arm's previous
entry on the same symbol is still open. OPEN rows are excluded.

## Hypotheses

**H-RG1, does the gate select?** d = mean(kept) − mean(regime_only), with a 90% bootstrap
interval (5,000 resamples, seed 2026, resampling each arm independently).
- **Gate PASSES (keep)** if d > 0 **and** the interval lies above 0.
- **Gate FAILS (no evidence it pays)** if d ≤ 0.
- **INCONCLUSIVE** otherwise. The gate stays.

**H-RG2, descriptive support:** regime_only against random_regime, by the H-CT1 rule.
It is reported, and it cannot overturn H-RG1.

**Decision rule across windows:** the gate FAILS overall only if H-RG1 FAILS in the
primary **and** the confirmation is not a PASS. It PASSES only if both windows PASS.

**Power guard:** n(regime_only) < 30 in the primary or < 10 in the confirmation →
INCONCLUSIVE for that window. Counts are reported for every arm.

## Reported regardless, never criteria

BTC alone, per-symbol means, how many BUYs fail `regime_counter` together with other
gates (and which), and the share of all NORMAL+ BUYs each gate touches.

## What the outcomes license

- **FAIL:** removing `regime_bearish` for spot is a data-yield design decision with
  supporting evidence, like the dead zone and the anti-chase vetoes. It is owner's call,
  and it should ship soon or wait for run 4, since every trading-path change restarts
  run 3's window.
- **PASS or INCONCLUSIVE:** the gate stays.

## Discard

- `regime_only` + `kept` + other-gate-blocked ≠ all NORMAL+ engine BUYs (partition
  check).
- Any `regime_counter` failure on a candle whose regime is not TRENDING/VOLATILE +
  BEARISH (gate logic mismatch).
