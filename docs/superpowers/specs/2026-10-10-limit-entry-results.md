# Results — do limit-order entries beat market entries on futures? (H-LO)

Scored against `2026-10-10-limit-entry-prereg.md` (`cec377f`). The instrument
`scripts/limit_ic.py` was committed at `df2f9e2` before the scored run, and smoke-tested
on 2020-09 only (outside both windows). Raw output: `2026-10-10-limit-entry-run/run.txt`
and `result.json`. Data: `perp_1h.csv`, sha256 `c0113801…7d4d`. 49,535 candles were
scored, 1,216 fires, every entry through the live futures exit. Discard checks: every
filled `l − m` equalled s. 0 entries hit the ambiguity rule, because the widened futures
TP1 is never reached in the bar after entry.

## Verdicts

| window | n | fill rate | d̄ (limit − market) | 90% CI | H-LO1 |
|---|---:|---:|---:|---|---|
| primary 2021–2024 | 352 | 96.3% | **+0.014pp** | [−0.041, +0.059] | INCONCLUSIVE |
| confirmation 2025–2026-08 | 120 | 98.3% | **+0.089pp** | [+0.070, +0.117] | PASS |

**Overall: INCONCLUSIVE.** The rule needs a primary PASS. **Market entries stay.**

## What it shows

- **Adverse selection is real, and it is the whole story.** In the primary window, the 13
  entries the limit missed earned **+1.44pp** on the market arm. Those are the trades
  that ran straight away in the trade's direction. The 339 the limit filled earned
  −0.15pp. Missing 4% of trades gave back most of the 0.07pp saving on the other 96%.
- In the confirmation window the 2 misses were losers (−1.24pp), so the limit won
  everything. That makes it a PASS on 2 trades of luck, not evidence.
- At taker 0.05% (s = 0.08), the primary d̄ is +0.024pp, still inside the noise.
- Point estimates were positive in both windows. The gain is at most ~0.02–0.09pp per
  trade, against a futures mean of about −0.09 to −0.12pp here. **Even if limit entries
  were adopted, futures would still lose.** Costs are not the main problem; the entry
  has no edge.

## Caveats on the fill model

A limit at the last close is crossed by the next 1h bar about 97% of the time. That makes
this model close to "a market entry at maker cost, minus the runaways". Real fills
are worse: latency, queue position, and post-only rejections when price has already moved.
That would push the true d̄ down, not up, so it does not change the verdict.

## What is licensed

Nothing for run 4. In the backlog this item moves from "untested" to "INCONCLUSIVE,
saving smaller than the futures loss". Re-test only if a future entry ever shows edge,
where 0.05–0.09pp would matter.
