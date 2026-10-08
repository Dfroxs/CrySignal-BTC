# Results — does the re-entry anchor need an age limit?

Scored against `2026-10-09-reentry-age-prereg.md`. Criteria committed at `49c8aec` before
any figure existed. The run started at that commit (first line of `2026-10-09-reentry-age-run/run.log`)
and finished in 18 minutes. Raw output: `run.log`, `result.json`.

## Verdict: INCONCLUSIVE, by the power guard

| | n | required |
|---|---:|---:|
| kept | **54** | ≥ 100 |
| stale_rejected | 110 | ≥ 30 |

`kept.n < 100`, so the pre-registration's power guard applies: **the verdict is
INCONCLUSIVE whatever the point estimate, and no other reading of H-R is reported.** The
figures are in `run.log` for the record. They decide nothing, and this document does not
interpret their direction.

The discard rule (`kept.n < 20`) did not fire, and nothing raised. The run stands. It is
simply underpowered.

**Consequence, as pre-registered:** `reentry_max_age_hours` stays `None` *as a result*.
Setting it remains open to the owner on the structural argument alone (an anchor three
weeks old is not "the setup that just exited"). If that happens, it is recorded as a
design decision, not as something this test showed.

## Why the sample is so small: a finding about the system, not the guard

Nine symbols and up to five years produced **54 resolved trades** in sequence: about one
per symbol per year. That is not a re-entry artefact. The engine fired 1,747 BUYs across
these spans, and the whole Phase 3 gate stack, plus one-position-at-a-time, admits about
3% of them. Two symbols contributed nothing at all. ICP's strict warmup left 17 months
with no BUY, and SUSHI fired 4.

That is the same order as live: run 1 opened 3 spot positions in 35 days, run 2 none in
5. **At this admission rate no single gate in this stack can ever be judged on its own
trade count.** That is the structural form of what CLAUDE.md already says about
thresholds.

## Descriptive, as pre-declared: never a criterion

| arm | n | mean (pp) | total (pp) | win % |
|---|---:|---:|---:|---:|
| unlimited, taken | 54 | −1.488 | −80.36 | 29.6 |
| aged (168h), taken | 154 | −0.749 | −115.29 | 35.1 |

Per the pre-registration, a cross-arm sequence comparison is not readable as selection,
because a different admit changes every later trade. Read only as what each arm would
have experienced: **both lose.** The aged arm trades roughly three times as often and
loses more in total. That is the warning `2026-09-24-entry-results.md` already gave:
unblocking a gate on an entry with no edge makes it trade more, not trade well.

Breadth (symbols with ≥ 5 stale rejections): kept > stale_rejected on 1 of 5.
Descriptive only.

## What this leaves for run 3

- The `NameError` fix (`5f84574`) ships regardless. It is a correctness defect, not a
  tuning question.
- The age-limit knob ships **off**. Turning it on is the owner's call, with the descriptive
  table above in view: it would end the spot lockout, and on this evidence it would also
  lose more in total.
