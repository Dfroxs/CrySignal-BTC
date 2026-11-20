# H-SR: does the repaired S/R condition predict? (pre-registration)

**Written 2026-11-20, before any return was computed for the repaired condition.** The
only figure that exists is how often it fires (below), measured without forward returns.

## What was wrong, and the repair

Condition 16 (`support_resistance`, ceiling 0.75 both modes) scores a bounce off support or
a rejection at resistance: price within 0.2×ATR of a level, closing in the bounce's
direction. It read its levels from `detect_support_resistance(df)`, which by default
(`tolerance=0.005`) returns only levels at least 0.5% away from the close. The proximity
window reaches about 0.27% on 1H and 0.37% on 4H, so the two ranges never overlap. It was
found in 2026-08 ("Found, not fixed") and measured live: 0% of 1,297 cycles since 10-09.

**Repair (owner, 2026-11-20: fix, not delete, so it yields data):**
- The condition finds its own levels with `tolerance=0.0`, the detector without its
  minimum distance.
- Nothing else changes: not the 0.2×ATR window, the bounce rule, the weight, the
  ceiling, or the thresholds.
- The `sr` other consumers read (TP caps, pyramid gate, terminal) is untouched.
- No value was chosen. 0.0 is the detector with its exclusion removed, so the condition
  tests what its own definition says.

**Firing rate, the only figure seen** (BTC, last ~2,950 bars, no returns computed):

| | 1H | 4H |
|---|---|---|
| old | 0% | 0% |
| repaired | 3.6% (BUY 1.5%, SELL 2.0%) | 3.2% (BUY 1.5%, SELL 1.7%) |

## H-SR-1: offline, the STEP 1 standard

`scripts/condition_ic.py --matrix --only support_resistance`, with the shipped code.
- **Statistic:** Welch t of the forward return after a BUY fire minus after a SELL fire.
  Positive means the condition points the right way.
- **Cells (futures, 1H):** BTC, ETH, SOL, BNB, XRP × 2024, 2025 (10 cells).
  `--horizons 3,6,12,24`, main horizon 6.
- **PASS:** positive t in **≥ 8 of 10** cells at horizon 6, **and** positive in ≥ 7 of 10
  at each of 3, 12 and 24. This is the bar `macd` failed in STEP 1.
- **FAIL:** anything else.
  - That includes a negative sign that holds: an inverted signal is a new hypothesis,
    not this one passing.
  - A cell with fewer than 20 fires on either side counts as not positive.
- **Spot (4H), same cells:** reported only.
- **Caveat:** overlapping forward windows make these t optimistic. The sign count is the
  criterion; |t| is not.

## H-SR-2: live, reported only

From the restart that ships the repair: every futures cycle where `support_resistance`
contributed, its signed 24h forward return (`+` = right way). It is reported at run 5's
scoring dates. It carries no verdict: at ~0.8 fires a day it cannot reach a useful n inside
90 days. It exists so the live data is kept, not judged.

## What each result licenses

- **PASS:** the repaired condition stays, and its weight may be studied in its own prereg.
- **FAIL:** it stays live for data (owner's reason for repairing it) until the next restart
  for another reason. Then it goes into `DISABLED_CONDITIONS`, which lowers both ceilings by
  0.75. No re-scoring on other cells, horizons or years to rescue it.

## Window and restarts

- Shipping the repair changes signals, so H-VG and H-VR/H-BS restart at that restart
  (their own amendment clauses).
- If it reaches the VPS before 2026-11-21 00:00 UTC, the scanner's new window (which
  begins then) is not cut. The hybrid arm's engine score includes this condition, so it
  would be cut otherwise.
