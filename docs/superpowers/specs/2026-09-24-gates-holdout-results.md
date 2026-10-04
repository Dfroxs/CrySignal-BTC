# Results — do the three anti-chase gates earn their place? (holdout)

Scored against `2026-09-24-gates-holdout-prereg.md`, criteria committed at `c55baaa`
before any holdout figure existed. Raw output in `2026-09-24-entry-run/`.

**The statistic:** `kept − rejected`. The gates earn their place only if what they throw
away is worse than what they keep. Positive favours the gates.

| run | kept | rejected | **kept − rejected** | 90% CI on the difference | breadth (n≥20) |
|---|---:|---:|---:|---|---:|
| tuning | −0.646 (n=1,872) | −0.518 (n=2,188) | **−0.128** | [−0.365, +0.122] | 3/7 |
| **primary** — time holdout | −0.080 (n=223) | −0.188 (n=251) | **+0.108** | [−0.627, +0.760] | 3/5 |
| secondary — market holdout | −0.756 (n=836) | −1.657 (n=1,028) | **+0.901** | [+0.125, +1.672] | 4/6 |

## Verdicts

**H-B2 on the PRIMARY holdout: INCONCLUSIVE.**
`kept > rejected` — the direction favours the gates — but the 90% interval
[−0.627, +0.760] contains zero. That is precisely the case the pre-registration named
INCONCLUSIVE, and it is the verdict. Both arms cleared the n≥100 power guard (223 and
251), so the guard did not fire; the interval is simply six times wider than the point
estimate. One year of nine symbols cannot resolve an effect this size.

**H-B2 on the SECONDARY holdout: PASS.**
`kept − rejected = +0.901pp` with the interval entirely above zero. Under the
pre-registered rule this passes. Under the same pre-registration it **cannot overturn the
primary**, because its protocol is weaker: the ten symbols are all younger than 200 weeks,
so the 1W EMA200 is unreal throughout and the HTF condition is permanently 0 for both
engine arms.

**Combined reading:** the direction is the same on both holdouts and opposite to tuning.
One holdout clears its bar and the stronger-protocol one does not.

## The tuning result did not reproduce — and was weaker than I reported

`2026-09-24-entry-results.md` recorded H-B as FAILED and I told the owner the answer to
"can it do more than pullbacks" was *remove the gates*. Two things are now on the record
against that.

First, **the tuning finding itself does not survive the sharper statistic.** The FAIL came
from a bare comparison of two overlapping pooled means. Measured properly, tuning gives
−0.128pp with an interval of [−0.365, +0.122] — which **contains zero**. I flagged at the
time that the criterion I had pre-registered was weaker than it should have been; this is
how much weaker.

Second, **both holdouts point the other way**, one of them decisively.

**The recommendation to remove `no_chase`, `anti_fomo` and `entry_wick` is withdrawn.**
There is no version of this evidence that supports it.

## H-A also reverses — post-hoc, not a criterion

Not pre-registered for the holdout, so this is an observation, not a verdict.

| run | spotsignal | random | intervals |
|---|---:|---:|---|
| tuning | −0.646 [−0.805, −0.492] | −0.443 [−0.481, −0.406] | disjoint, **against** the engine |
| primary holdout | −0.080 [−0.593, +0.401] | −0.807 [−0.892, −0.717] | disjoint, **for** the engine |

On tuning the entry was significantly worse than a dart. On the holdout year, under the
identical protocol, it was significantly better — by 0.727pp. The engine barely moved
(−0.646 → −0.080); what moved was **random entry**, from −0.443 to −0.807. The holdout year
punished indiscriminate entry far harder, and selectivity was worth something there and
nothing before.

(The secondary holdout also puts spotsignal above random, +0.119pp. The pre-registration
excluded that comparison on that dataset in advance, and it stays excluded.)

## What this run actually establishes

**Not** that the gates work, and **no longer** that they don't. What reproduces across all
three datasets is that the sign of every headline flips with the period:

- the gates cost 0.128pp on 2018–2025 and earned 0.108–0.901pp on 2025–2026;
- the entry lost to random by 0.203pp on 2018–2025 and beat it by 0.727pp on 2025–2026.

That is the same result `CLAUDE.md` records from STEP 1 — *no component's predictive power
survived a change of market or period* — arriving now for the gates and for the assembled
entry, which STEP 1 never tested.

**Nothing in `config.py`, `signals/`, `trading/` or `run_bot.py` changed,** and on this
evidence nothing should. An INCONCLUSIVE primary licenses no edit in either direction.

## Limits

One year on the primary, and 223 kept entries is thin — the CI reflects that honestly.
Both holdouts are crypto, spot, long-only, 4h, at a fixed 4.3 threshold while the live
controller moves 3.0–7.0. Entries are simulated independently, so this measures an entry
POINT and says nothing about portfolio sequencing. Breadth figures in the table exclude
symbols with fewer than 20 entries in either arm; counting all symbols inflates them to
6/9 on both holdouts on the strength of symbols with one trade.
