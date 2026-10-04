# Pre-registration — the 1.2× confidence dead zone

Written before `scripts/deadzone_ic.py` (committed at the previous commit) has produced a
single figure. Criteria below are fixed from here.

## The defect

The engine fires a BUY at `strength ≥ 1.0 × threshold`: a Telegram alert goes out, a
`cycle_log` row is written. Phase 3 opens a position only at `strength ≥ 1.2 × threshold`
(`get_signal_confidence` → NORMAL). **The band between is two bars for one decision.**

Paper run 1, 35 days: futures fired 29 signals and opened **zero** positions. `signal_blocks`
records `confidence_first` 25 times — the single largest block reason. The mean
strength/threshold ratio was **1.117** against a bar of 1.20: the distribution sits just
under it.

The adaptive controller compounds it. `_update_threshold_state(signal['type'])` counts
signals that **fire**, not positions that **open**, so it raised the bar in response to
activity that never happened.

## Two questions, deliberately separate

**H1 — the gate costs money.** Dead-zone entries (`1.0 ≤ r < 1.2`) outperform a
count-matched random baseline.
- **PASS** if `deadzone.mean − random.mean > 0` **and** the 90% bootstrap interval on that
  difference lies entirely above zero.
- **FAIL** if the difference is ≤ 0.
- **INCONCLUSIVE** if positive but the interval contains zero.

**H2 — the ratio is informative.** Entries above the bar (`r ≥ 1.2`) outperform entries
below it.
- **PASS** if `opened.mean − deadzone.mean > 0` **and** the 90% interval clears zero.
- **FAIL** / **INCONCLUSIVE** by the same rule.

**Power guard:** fewer than 100 resolved entries in any arm of a comparison makes that
comparison INCONCLUSIVE regardless of the point estimate.

They can both fail, and that is a real outcome, not a null one: it would say the bar is
**arbitrary but harmless** — the dead zone is then a reporting defect (alerting on signals
that cannot open, and feeding a controller that miscounts them) rather than a financial
one. The fix would be to stop firing in that band and to count opens, not to widen it.

## Data, and its exposure

Nakhoda's OKX 4h cache. **Primary:** the nine mature `tuning` symbols before 2025-08-30 —
the largest sample. **Confirmation:** the same symbols, 2025-08-30 → 2026-08-30, the
window the tuning run never touched.

*Declared honestly:* this is the **third** hypothesis put to the tuning set (after the
entry experiment's H-A/H-B/H-C and the gate comparison). Looking repeatedly at one dataset
inflates the chance of a false positive. The confirmation window is therefore not optional
decoration: **a PASS on the primary that the confirmation window contradicts is reported as
INCONCLUSIVE, not as a pass.**

## Method, and what it deliberately removes

- The threshold is **fixed at `SPOT_THRESHOLD` (4.3), not adaptive.** The live controller
  moves the bar, so a ratio measured against a moving threshold would confound the gate
  with the controller. Fixing it isolates the gate, which is what is being judged.
- Entries are simulated **independently** — no `open_until` blocking. `CLAUDE.md` records
  that sequencing makes comparisons here unreadable, because a lower bar fires an earlier
  signal whose position swallows the window a later one would have used.
- Every arm goes through the **same exit simulator and the same costs** the bot uses.
- Spot only, 4h, long-only. The defect is worse on futures, but no historical replay can
  score the futures-only conditions, so a futures test would measure a different system.

## What would make me discard the run

- Any arm resolving fewer than 20 entries pooled.
- The random baseline's mean differing by more than 0.5pp between the first and last ten
  seeds, which would mean the seeds are not sampling one population.
- The dead-zone and above-bar buckets not partitioning the engine's BUYs exactly.

## What a result licenses

A **PASS on H1** would be the first evidence in this project that a gate is destroying
value, and would justify a pre-registered test of removing the 1.2× bar.

A **FAIL on H1 with a PASS on H2** would say the bar is doing its job and the dead zone is
working as intended — leave it.

**Both failing** licenses exactly one change, and it is not a trading change: stop emitting
a fired signal that cannot open, and make the adaptive controller count opens. Neither
moves a threshold, a weight or a gate.

Nothing here licenses touching the running bot. Run 2 started 2026-10-04 and is pinned.
