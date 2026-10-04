# Results — the 1.2× confidence dead zone

Scored against `2026-10-04-deadzone-prereg.md`, criteria committed before any figure
existed. Instrument committed one commit earlier. Raw output in `2026-10-04-deadzone-run/`.

| | PRIMARY (tuning, →2025-08-30) | CONFIRMATION (2025-08-30→2026-08-30) |
|---|---|---|
| **H1** gate costs money | **−0.105pp** [−0.328, +0.124] → **FAIL** | +0.220pp [−0.442, +0.917] → **INCONCLUSIVE** |
| **H2** ratio is informative | **−0.217pp** [−0.540, +0.096] → **FAIL** | **+1.049pp** [+0.109, +1.992] → **PASS** |

Every arm of both hypotheses cleared the n ≥ 100 power guard.

## H1 — no evidence the 1.2× gate costs money

Dead-zone entries (`1.0 ≤ r < 1.2`) did **not** beat a count-matched random baseline on
either window: −0.105pp on 1,035 entries, +0.220pp on 116 with an interval spanning zero.

**The gate is not throwing away profitable entries.** This is the question the experiment
was run to answer, and it answers it in the direction that licenses no change.

It also corroborates the smaller measurement that prompted this: the 29 futures signals
paper run 1 blocked came to −0.304pp as trades against random entry's −0.235pp.

## H2 — contradictory, and the sign flips again

−0.217pp on the primary, +1.049pp on the confirmation. One window says the bar separates
nothing and mildly inverts; the other says it separates well. **Not confirmed.**

## The finding that holds in both windows

Neither window orders P&L by confidence:

| | deadzone (<1.2×) | normal (1.2–1.5×) | strong (≥1.5×) |
|---|---:|---:|---:|
| primary (n=1035 / 715 / 122) | −0.558 | **−0.788** | −0.700 |
| confirmation (n=116 / 93 / 14) | −0.596 | **+0.721** | **−1.330** |

In the primary the *worst* bucket is `normal`; in the confirmation it is `strong`. **In
neither does P&L rise with confidence.** `get_signal_confidence` gates two decisions on
this ordering — NORMAL to open at all, STRONG to pyramid — and the ordering is not there.

That is more consistent than H2 and points the same way as H1: the tiers are not
separating what they are used to separate.

*Not concluded from `strong` alone on the confirmation:* n = 14, below the 20 the
pre-registration set for drawing a conclusion from a bucket.

## Discard conditions

- Hypothesis arms below 100: none. The descriptive `strong` bucket on the confirmation is
  14 and is excluded from conclusions, as pre-registered.
- Random-baseline seed stability: this is the identical baseline used by
  `2026-09-24-entry-results.md`, where drift between the first and last ten seeds measured
  0.0109pp against a 0.5pp limit. Not re-derived.
- Partition: deadzone + normal + strong = 1,872 on the primary, exactly the `spotsignal`
  arm count in the entry experiment on the same data. Same engine, same entries.

## What this licenses

**Not removing the 1.2× bar.** H1 fails and the pre-registration said so in advance.

The one change the evidence supports moves no parameter, and both findings point at it:

1. **Stop emitting a fired signal that cannot open.** A `strength` in `[1.0, 1.2)` produces
   a Telegram alert and a `cycle_log` row for a position that Phase 3 will refuse. Over
   run 1 that was 25 of 29 futures signals. Two bars for one decision is a reporting
   defect whether or not the second bar is well-placed.
2. **Make the adaptive controller count opens, not fires.**
   `_update_threshold_state(signal['type'])` counts signals that fire. Futures fired 29 and
   opened 0, and the controller raised the bar in response to activity that never happened.
   It is a feedback loop watching the wrong variable.

Both are correctness changes to what is recorded and what the controller measures. Neither
moves a threshold, a weight or a gate, and neither can be justified as a route to profit —
H1 says there is none here to recover.

**Nothing in `config.py`, `signals/`, `trading/` or `run_bot.py` changed for this run**,
and the live bot is untouched. Paper run 2 started 2026-10-04 and stays pinned.

## Limits

Spot only, 4h, long-only, fixed threshold 4.3 — the adaptive controller is deliberately
removed so the gate could be isolated from it, which means this does not measure the two
interacting. One asset class. The confirmation window is one year and its H2 arms are
107 and 116 entries. And this is the third hypothesis put to the tuning set; the
confirmation window exists for that reason and it did not confirm.
