# Results: is spot's TP1 too far away? (H-TP)

Scored against `2026-10-15-tp1-distance-prereg.md` (`34ccb01`). Instrument
`scripts/tp1_ic.py` committed at `9e1b752` before the scored run, with a smoke run that
produced counts only. Raw output: `2026-10-15-tp1-distance-run/run.txt`, `result.json`.

**Discard check passed:** Arm C's baseline reproduces H-SR's `kept` arm exactly (347 at
−0.165pp, 43 at −0.515pp). Every one of the 30 synthetic cells produced a row (two
fetches needed one retry each). Unresolved entries: 0 in every arm. The candidate is not
inert on engine entries: TP1 moved on 98–99% of them.

## Verdict: INCONCLUSIVE

| arm | data | cells favouring | pooled paired diff | TP1 hit (base → cand) | criterion |
|---|---|---|---|---|---|
| A | synthetic, 2024–2025, 5 assets | **9 / 10** (need 8) | **+0.0995pp** (need ≥ +0.05) | 13% → 32% | **met** |
| B | synthetic, 09-21 grid, 2020–2023 | **18 / 20** (need 16) | **+0.1025pp** | 14% → 31% | **met** |
| C | engine entries, primary (n=347) | — | **−0.0195pp** | 17% → 33% | must be > 0: **fails** |
| C | engine entries, confirmation (n=43) | — | **−0.0772pp** | 14% → 30% | must be > 0: **fails** |

Neither A nor B pooled ≤ 0, so it is not a FAIL. Arm C, the guard, is negative in both
windows, so it is not a PASS. **Per the pre-registration: INCONCLUSIVE, nothing is
licensed, and no other R multiple may be tried on these grids or windows.**

## What it says

- **On price itself, a nearer TP1 is better, and consistently so.** Over ~9,900 paired
  synthetic entries in 30 cells across six years and five assets, TP1 at +2.25 ATR beat
  +3.75 ATR in 27 cells, by ~+0.10pp per entry. That is twice the pre-registered size bar.
  It more than doubles how often TP1 is reached (≈13% → 32%). BTC alone: +0.08pp in both
  arms. As a property of 4h crypto price behaviour under this exit machinery, it held.
- **On the entries this engine actually takes, it does not.** On H-SR's kept trades the
  candidate is slightly worse in both windows (−0.02pp over 347, −0.08pp over 43). That is
  far inside noise, but the sign is wrong, and the guard exists for exactly this case.
  One reading fits H-RT: 61% of the engine's spot entries are range-top breakout-coil
  buys. A trade of that kind that works tends to run, so cutting TP1 shortens the
  winners that pay for the losers. Random entries carry no such selection. This reading
  is a post-hoc explanation, not a finding.
- **The harm is not where the guard was supposed to catch contamination.** The guard was
  meant to stop a candidate that only looks good on the data that suggested it. Instead
  the candidate looks good everywhere **except** that data. So the contamination
  disclosed in the prereg did not inflate this result. If anything, it biased it against
  the candidate.
- **Every baseline still loses** (synthetic −0.46 / −0.55pp, engine −0.17 / −0.52pp per
  trade). A nearer TP1 shrinks the loss on random entries. It does not turn any arm
  profitable.

## What is licensed

Nothing changes. Spot TP1 stays at 2.5 × risk.

Recorded for run 4, as a lead and not a candidate. The synthetic result is strong and
consistent, and it disagrees with the engine-entry guard. Whether a nearer TP1 helps
**this** system now depends on the entry, and the entry is unproven against random
(H-B, scored 2026-11-08). If H-B or a later entry change shows the engine's entries
behave like random ones, H-TP's synthetic arms are the evidence to cite. Re-testing
needs a new pre-registration on engine entries the bot has not yet produced, for example
run 3 and run 4's own trades.
