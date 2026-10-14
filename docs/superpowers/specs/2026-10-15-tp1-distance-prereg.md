# Pre-registration: is spot's TP1 too far away? (H-TP)

**Written 2026-10-15, before the instrument exists and before any figure below has been
computed.**

## Why

Two observations, both made before this document:

1. **Run 3's three spot losses** (#5–#7, 2026-10-11 to 10-14) never reached TP1. TP1 sat
   at +3.75 ATR (+3.1 on the pyramid), above the 72h range's high. Their best excursions
   were +0.86 to +2.15 ATR.
2. **H-RT's descriptives** (`2026-10-14-range-top-results.md`), over H-SR's `kept`
   universe: TP1 was hit on **8–21%** of spot trades in every arm. TP1 lay above the 72h
   high on 80–100% of them. Mean best excursion was **1.9–2.3 ATR**.

Spot today: risk unit = 1.5 ATR (`atr_multiplier`). TP1 = entry + 2.5 × risk = **+3.75 ATR**
(`take_profit_rr` 2.5), unless capped below resistance. TP2 = twice TP1's distance. At TP1,
50% closes and the trail moves to entry − 0.5 ATR.

**What this does not repeat.** On 09-21, three spot exit rules FAILED: a longer hold cap,
a looser post-TP1 trail, and no partial at TP1. None of them moved TP1. This tests TP1's
**distance** only.

**Contamination, disclosed.** The candidate below (+2.25 ATR) sits inside the 1.9–2.3 ATR
mean excursion that H-RT measured on its universe. Arm C below reuses that universe, so
Arm C is a guard, not evidence. The evidence has to come from arms A and B, which use
different entries and different data.

## The one candidate (fixed now)

**TP1 at 1.5 × risk = +2.25 ATR** (`take_profit_rr` 2.5 → 1.5), TP2 at twice TP1's
distance (+4.5 ATR). Everything else stays exactly as live: stop, partial split, the
post-TP1 trail, the hold cap, the volatility exit and costs.

Why 1.5: it is the R:R floor the engine already enforces (`rr < 1.5` vetoes a signal), so
it is the nearest TP1 the system's own rules already accept as worth taking. One
candidate only: no sweep of R multiples is run or reported.

**Applied at open, not in the engine.** In the engine, a nearer TP1 would also change
which signals survive the R:R veto. With the stop at its 2.5 ATR cap, R:R would fall
from 1.5 to 0.9 and the signal would be vetoed. That would test two things at once. So
the candidate rewrites only the levels of a position that has already been opened, as
`apply_futures_exit_geometry` does for futures. Entries, gates and the R:R veto stay
exactly as live.

For a signal with engine levels (`take_profit`, `tp2`):
`TP1' = min(take_profit, entry + 1.5 × 1.5 × ATR)` and
`TP2' = min(tp2, entry + 2 × (TP1' − entry))`. A target already capped below
resistance is never moved further out.

## Arms

All arms use `backtest._simulate_forward` in spot mode (4h, max hold 18 bars, live spot
costs). Each entry runs twice, once with baseline levels and once with the candidate's.
The comparison is **paired**: `diff = pnl(candidate) − pnl(baseline)` per entry.

- **A — synthetic entries, fresh data.** `scripts/exit_ic.py`'s method: every 6th 4h
  candle after warmup becomes a BUY at `exit_ic._signal_at` levels. Assets BTC, ETH, BNB,
  XRP, LINK (USDT, Binance), years **2024 and 2025**: **10 cells**. No exit test in this
  repository has used these years.
- **B — synthetic entries, the 09-21 grid.** Same method on 09-21's spot cells: the same
  five assets, 2020–2023, **20 cells**. That grid has tested exit rules before, but never
  TP1's distance.
- **C — engine entries (guard).** H-SR's `kept` universe on Nakhoda's OKX cache, built
  exactly as `scripts/range_top_ic.py` builds it (de-duplicated with baseline exits, as
  live would take them). Primary window to 2025-08-30 and confirmation window
  2025-08-30 → 2026-08-30. **Contaminated** (see above).

Unresolved entries (OPEN rows, zero ATR) stay in the sample at 0.0 and are counted, per
09-21 §4.2. They are never grounds to drop a cell. Cells that fail to fetch are retried
until they produce a row, never dropped.

## Criteria (09-21 §4, adapted)

**Arm A** and **Arm B**, each:
1. **Sign consistency:** the paired mean diff favours the candidate in ≥ 80% of the arm's
   cells (≥ 8 of 10 for A, ≥ 16 of 20 for B).
2. **Effect size:** the pooled paired mean (every entry concatenated once, so
   entry-weighted, over **all** entries, never `n_eff`) is **≥ +0.05pp per entry**.

**Arm C:** the paired mean diff is **> 0 in both windows**. It has no size bar and
no interval. As a contaminated guard, it can veto but never carry a PASS.

**Verdict:**
- **PASS** if A meets both criteria, B meets both, and C holds.
- **FAIL** if A's pooled paired mean is ≤ 0, or B's is ≤ 0.
- **INCONCLUSIVE** otherwise.

Reported, never criteria: each arm's TP1-hit rate under both levels, `n_eff`, the
unresolved count, and BTC-only figures. A pooled t-statistic is not a criterion: with
thousands of paired entries any trivial effect clears it, so effect size binds.

## Discard

- **Arm C fidelity:** C's baseline must reproduce H-SR's `kept` arm, n = 347 at −0.165pp
  and n = 43 at −0.515pp to 3 dp. Any difference → DISCARDED. Nothing it depends on has
  changed since `571b024` except the futures-only pruning of 2026-10-14, which cannot touch
  spot scoring (`test_basis_and_taker_are_pruned_from_futures_and_spot_is_untouched`).
- **Candidate inert:** if TP1' equals the baseline TP1 on more than 90% of Arm C's entries
  (every target already capped closer), the candidate barely acts on engine entries and
  C is reported as such. That is not a discard of A or B.

## What each verdict licenses

- **PASS:** spot TP1 at 1.5 × risk becomes a **tested change** in `run4-backlog.md`. It
  is implemented as a spot exit geometry applied at open, with the engine's R:R veto
  untouched. The owner may also ship it mid-run, which restarts every spot-fed
  hypothesis from the deploy, as on 2026-10-14 for futures. It is not applied to futures,
  whose exit geometry has its own test.
- **FAIL or INCONCLUSIVE:** nothing changes. No other R multiple may be tried on these
  grids or windows: that would be the sweep this document forbids.

## Instrument

`scripts/tp1_ic.py`, reusing `exit_ic` (synthetic entries, `_signal_at`) and
`range_top_ic` / `spot_gates_ic` (Arm C), committed before the scored run. Its smoke run
produces counts only: entries per cell for one A cell, and C's universe size on the first
400 BTC candles. Raw output goes to `2026-10-15-tp1-distance-run/`.
