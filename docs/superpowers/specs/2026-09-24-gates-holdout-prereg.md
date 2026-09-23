# Pre-registration — do the three anti-chase gates earn their place? (holdout)

Confirmatory test of H-B, which FAILED on the tuning set
(`2026-09-24-entry-results.md`). Written before any holdout figure exists.

## The sharper measurement

The tuning run compared two overlapping pooled means and found a 0.069pp difference with
intervals that overlapped heavily. I recorded at the time that the criterion I had written
was weaker than it should have been. This test fixes that.

The gated arm is a strict SUBSET of the ungated one — the gates can only turn a BUY into a
HOLD. So the ungated arm partitions exactly into **kept** and **rejected**, and the real
question has a direct form:

> The gates earn their place only if what they REJECT performs worse than what they KEEP.

`kept − rejected` is the statistic. Positive means the gates filtered out bad entries.
Negative means they threw away the better ones.

**Prediction from tuning, by arithmetic identity** (ungated mean × n = kept×n_k +
rejected×n_r, with −0.577 × 4060 and −0.646 × 1872): rejected ≈ **−0.518pp** against kept
**−0.646pp**, i.e. `kept − rejected ≈ −0.128pp`. The gates were throwing away the better
entries. The holdout measures this directly rather than deriving it.

## Two holdouts, two protocols — both stated before running

**PRIMARY — time holdout.** The nine mature `tuning` symbols (BNB has no warmup),
2025-08-30 → 2026-08-30. ~19,710 candles. **Protocol identical to the tuning run**: strict
1D+1W EMA200 warmup, threshold 4.3, same exits, same costs. Earlier candles are loaded to
warm the indicators and are not scored. Untouched by the tuning run, which stopped at
2025-08-30.

**SECONDARY — market holdout.** Nakhoda's ten locked holdout symbols, full span to
2026-08-30. ~43,158 candles.

*Why this one needs a different protocol, and what it costs:* every one of those ten is
younger than 200 WEEKS, so under the strict rule **zero** of them can be evaluated — that
is a measured fact, not an estimate. Admitting them requires warming on the 1D EMA200
alone, after which the 1W trend reads BEARISH throughout, `aligned` never fires, and the
HTF scoring condition is permanently 0.

That penalty falls on **both engine arms identically**, so `kept − rejected` remains a
controlled comparison. It does **not** fall on the random or Donchian arms, so on this
holdout those two may not be compared with the engine at all. Any such comparison is
excluded from the verdict in advance.

## Criteria

**H-B2 — the three anti-chase gates earn their place.**

- **PASS** if, on the PRIMARY holdout, `kept.mean > rejected.mean` **and** the 90% bootstrap
  interval on `kept − rejected` lies entirely above zero.
- **FAIL** if `kept.mean <= rejected.mean` on the primary.
- **INCONCLUSIVE** if the direction favours the gates but the interval contains zero.
- **Power guard:** if either arm resolves fewer than 100 entries on the primary, the verdict
  is INCONCLUSIVE regardless of the point estimate, and no other reading may be reported.

The SECONDARY holdout is scored by the same rule and reported separately. It can corroborate
or contradict the primary; it cannot overturn it, because its protocol is weaker.

**Breadth (descriptive, not a criterion):** the share of symbols where
`kept.mean > rejected.mean`.

## What a FAIL licenses, and what it does not

A FAIL would say the three gates reject entries that were better than the ones they keep,
on data neither they nor I have seen — reproducing the tuning direction out of sample.

It licenses **one** change: removing `no_chase`, `anti_fomo` and `entry_wick`. It does not
license shipping the result, because H-A already established that both engine arms lose to
random entry. Removing the gates would make a losing entry less losing. That is worth
knowing and is not worth trading.

## What would make me discard the run

- Either engine arm resolving fewer than 20 entries pooled on a holdout.
- `split_by_gates` raising — the gated arm not being a subset of the ungated one.
- Any primary-holdout figure differing from the tuning run's protocol in any respect other
  than the date window.
