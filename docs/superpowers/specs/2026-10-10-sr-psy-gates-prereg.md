# Pre-registration: do the spot S/R and psychology-SL gates earn their place? (H-SR, H-PSY)

**Written 2026-10-10, before the instrument exists and before any figure below has been
computed.**

## Why

In the 24h to 2026-10-10 16:01 UTC, live spot fired a BUY on 7 consecutive 4H bars, 6 of
them NORMAL or STRONG, and opened nothing. Five different gates refused them. Two of
those gates have never been tested on spot:

- **`sr_first`** (live gate 0e): BUY refused when the entry sits within 1× ATR below
  the nearest resistance (`_check_sr_entry_risk`, `sr_entry_risk_atr` 1.0). It refused
  12:01 (STRONG, 1.54×) and 16:01 (NORMAL, 1.36×).
- **`psy_sl_first`** (live gate 0d): BUY refused when the stop-loss sits within 0.15%
  below a round number (`_check_psychology_sl_risk`, step $1,000). It refused 08:01
  (NORMAL, 1.37×).

`regime_bearish` has its own test (H-RG; H-RGL live). The futures-gates test (2026-10-09)
covered these gates on futures only and was INCONCLUSIVE. The anti-chase test
(2026-09-24) covered `no_chase`/`anti_fomo`/`entry_wick`, not these gates.

H-RG's harness cannot be reused as is: it called the engine with `sr=None`, so
`sr_first` could never fire there (0 of 639 regime-blocked BUYs also failed it), and
the S/R scoring condition never scored either.

## Data (fixed now)

- The sibling project's hash-verified OKX 4h cache, loaded by `scripts/entry_ic.py`'s
  `load_symbol`: its 10 `TUNING` symbols (BTC, ETH, SOL, BNB, OKB, ICP, SUSHI, NEAR, UNI,
  DOGE). Nakhoda's locked market-holdout symbols are not read.
- HTF (1D, 1W) resampled from the 4h base (`entry_ic.build_htf`, strict warmup).
- **Scoring as live does it:** `backtest._score_candle` (the last `LIVE_BASE_BARS` = 499 closed bars with
  indicators computed over them alone, `detect_support_resistance` passed to the engine,
  HTF as of that bar), `mode='spot'`, fixed `SPOT_THRESHOLD`, `disabled=None`, so
  `config.VETOES_DISABLED` applies (spot anti-chase off, as deployed since `316e0dc`).
- **Primary window:** from each symbol's warmup to 2025-08-30. **Confirmation:**
  2025-08-30 → 2026-08-30.

## The psychology step

Live uses a fixed $1,000 step, which only means "round number" for BTC. For DOGE at
$0.10 the gate can never fire. Fixed now:

- **BTC: $1,000**, exactly as live, at every price.
- **Every other symbol:** `10 ** (floor(log10(entry)) − 1)`, the round numbers of the
  price's own scale (ETH at $3,000 → $100, SOL at $150 → $10, DOGE at $0.10 → $0.01).
- Buffer 0.15%, as live. `backtest._failing_gates`'s own `psy_sl_first` (hardcoded
  $1,000) is replaced by this rule for every symbol. For BTC the two are identical.

## Arms (per gate G ∈ {`sr_first`, `psy_sl_first`}, per window)

Every engine BUY with confidence ≥ NORMAL. Its failing gates come from
`backtest._failing_gates(sig, "spot", window, None)`, with the psychology rule above,
ignoring `confidence_first` (it precedes these gates live) and `reentry_first` (needs
trade history, not simulated).

- **`only_G`**: BUYs whose failing gates are exactly `[G]`. These are what removing G
  alone would open.
- **`kept`**: BUYs that fail no gate. Shared by both gates.
- **`random_G`**: count-matched random bars (20 seeds, pooled per window, drawn per
  symbol in proportion to `only_G`'s count) where G's condition holds on the bar's
  `exit_ic._signal_at` levels, excluding the bars in `only_G`. For `sr_first`: the
  bar's resistance is within (0, 1× ATR] above its close. For `psy_sl_first`: the
  `_signal_at` stop-loss is within 0.15% below a round number of the step above.

All arms go through `backtest._simulate_forward` in spot mode (live spot exit and costs,
max hold 18 bars). Engine arms are de-duplicated per symbol: no new entry while that
arm's previous entry on the same symbol is still open. OPEN rows are excluded and
counted.

## Hypotheses (identical rules for each gate)

**H-x1, does the gate select?** d = mean(`kept`) − mean(`only_G`), with a 90% bootstrap
interval (5,000 resamples, seed 2026, each arm resampled independently).
- **PASS (keep)** if d > 0 **and** the interval lies above 0.
- **FAIL (no evidence it pays)** if d ≤ 0.
- **INCONCLUSIVE** otherwise.

**H-x2, descriptive:** `only_G` against `random_G`, by H-RG2's rule. Reported, and it
cannot overturn H-x1.

**Across windows:** the gate FAILS overall only if H-x1 FAILS in the primary **and** the
confirmation is not a PASS. It PASSES only if both windows PASS. Otherwise INCONCLUSIVE.

**Power guard:** n(`only_G`) < 30 in the primary or < 10 in the confirmation →
INCONCLUSIVE for that window. Every arm's count is reported.

The two gates are tested separately, each at 90%. No multiple-comparison correction is
applied, so one false PASS or FAIL in two tests is not unlikely and must be read that
way.

## Discard

- The instrument's live-fidelity check fails: on BTC, the replaced psychology rule must
  give the same verdict as `_failing_gates`'s own on every BUY. Any difference →
  DISCARDED.
- `sr_first` fires on no engine BUY in either window → the instrument does not reach
  the gate → DISCARDED.

## What each verdict licenses

Run 3's clause discards the run on any change to Phase 3 logic or `config.py` on the
server. So no verdict here changes the live bot during run 3 by itself.

- **PASS:** the gate stays, in run 3 and run 4.
- **FAIL overall:** the gate goes into the run-4 backlog as "disable for spot". The
  owner may instead apply it now as a recorded data-yield design decision, which
  restarts run 3's scoring window as on 2026-10-09 10:55 and 12:34.
- **INCONCLUSIVE:** the gate stays by default. The owner may still remove it as a
  data-yield design decision under the same restart rule, recorded as such and never
  as a test result.

## Instrument

`scripts/spot_gates_ic.py`, committed before the scored run, with a smoke run that
produces counts only (first 400 candles of BTC).
