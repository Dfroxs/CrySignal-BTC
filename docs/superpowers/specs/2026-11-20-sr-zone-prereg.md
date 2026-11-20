# H-SR2: S/R zones the way a trader draws them (pre-registration)

**Written 2026-11-20, before any code or figure exists for this condition.** It follows
H-SR, which FAILED (`2026-11-20-sr-fix-results.md`). That test judged one crude rule:
swing points of the last 50 base bars, within 0.2×ATR, any up-close. The owner's point
stands: S/R matters in technical analysis. How traders use it differs from that rule in
three ways, and this design takes all three.

1. **Levels come from the higher timeframe**, not the bars being traded.
2. **A level counts only once it has been tested more than once.** Pivots cluster into a
   zone, and a zone needs at least 2 touches. A highs-and-lows mix counts too
   (polarity flip: old resistance becomes support).
3. **The trigger is a rejection at the zone:** the bar tests it and closes back away from
   it in the bounce's direction, not just near it.

No value below was chosen by looking at data. Each is a textbook default, stated with its
reason. None may be changed after a figure exists. A different design is a new prereg.

## The condition `sr_zone` (fixed now)

**Higher-timeframe bars**
- Built from the closed base bars: futures 1H → 4H, spot 4H → 1D.
- Only buckets fully covered by closed base bars count, so the bucket still forming is
  never read.
- Last **90** HTF bars: 15 days on 4H, 90 days on 1D. That is about a quarter of the
  market's recent structure, the span traders usually mark.

**Pivots:** a 5-bar fractal, the standard Williams fractal. An HTF bar is a pivot high if
its high is strictly the highest of the 2 bars before and the 2 after it, and a pivot low
the same way with lows.

**Zones**
- All pivot prices, highs and lows together, sorted and merged greedily: a pivot within
  **0.5 × ATR14(HTF)** of the zone's running mean joins it.
- Zone price = the mean of its pivots. Touches = the number of pivots in it.
- **Valid zone: ≥ 2 touches.**

**Trigger, on the last closed base bar** (ATR = ATR14 of the base timeframe):
- **BUY (support rejection):** a valid zone Z sits in all of these places:
  - the bar's low is within Z ± 0.5×ATR;
  - the close is above Z;
  - the bar closed up (close > open).
- **SELL (resistance rejection):** the mirror. The high is within Z ± 0.5×ATR, the close is
  below Z, and the bar closed down.
- If both fire on one bar, it scores nothing.

**Weight:** ±0.75, as the old condition. The weight plays no part in this test, which reads
only the sign.

**Status:** added to `CONDITION_MAX` (0.75, 0.75) and **to `DISABLED_CONDITIONS` from the
first commit**.
- No score, ceiling or threshold moves, so nothing restarts for it.
- Live, it is recorded in `cycle_log.disabled_contributions` from the next restart made
  for another reason.
- `scripts/condition_ic.py --include-disabled` scores a disabled condition from that same
  record.

## H-SR2-1: offline, the STEP 1 standard (same as H-SR-1, so the two compare)

```
condition_ic.py --matrix --only sr_zone --include-disabled --mode futures \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT --years 2024,2025 \
  --horizons 3,6,12,24 --horizon 6
```

- **Statistic:** Welch t of the forward return after a BUY fire minus after a SELL fire.
  Positive means the condition points the right way.
- **PASS:** positive t in **≥ 8 of 10** cells at horizon 6, **and** ≥ 7 of 10 at each of
  3, 12 and 24.
- **Thin cells:** a cell with fewer than 20 fires on either side counts as not positive.
- **FAIL:** anything else. A negative sign that holds is a new hypothesis, not a pass.
- **Power guard:** measured before the IC run, with no returns read. If BTC 1H fires on
  fewer than **1%** of bars, the verdict is **INCONCLUSIVE (no power)** and no IC is run.
- **Spot (4H on 1D levels), same cells:** reported only.
- Overlapping forward windows make t optimistic; the sign count is the criterion.

## What each result licenses

- **PASS:** `sr_zone` may leave `DISABLED_CONDITIONS` at the next restart. That is a scoring
  change: the ceilings rise by 0.75 and the thresholds follow, and it ships recorded as
  licensed by this test. Its weight is a separate prereg.
- **FAIL:** S/R as a scoring bonus is closed in both forms tested. It stays disabled and
  recorded.
  - The next S/R idea would be a different role, for example a Phase 3 location filter.
    That needs its own prereg.
  - Nothing is re-scored on other cells, horizons, years or parameters.
- **Either way:** S/R keeps its current non-scoring roles (TP caps, `sr_first`, pyramid
  gate). This test does not touch them.
