# Exit mechanics — pre-registration

**Date registered:** 2026-09-22
**Branch:** `develop`
**Status:** committed before any confirmatory cell has been run. No cell of the
40-cell grid below has been read for any of the three hypotheses.

This document fixes the hypotheses, the exact rules `scripts/exit_ic.py` runs,
the cell grid, and the numeric pass/fail criteria, before Task 6 executes the
confirmatory run. Failure on any criterion ends that hypothesis. Criteria are
not revised after results are seen.

---

## 1. What was inspected before this was written, and what that does and does
   not license

`BTC` spot and futures for **2024 and 2025** were inspected during design
(`docs/superpowers/specs/2026-09-21-exit-mechanics-design.md`, section 2) to
find the `TIME_EXIT` defect and motivate H1–H3. That inspection licenses
**hypothesis selection only**. Those two symbol-years are burned exploratory
data:

**BTC 2024–2025 may not be used to judge any hypothesis in this document.**

The confirmatory grid below excludes them entirely. Non-BTC 2024–2025 is a
separate **reserve holdout** — untouched, not inspected, and not to be read
unless a hypothesis passes its confirmatory grid and a second independent
confirmation is wanted.

---

## 2. The cell grid

**A cell is one `(asset, year, mode)` triple.**

| Axis | Values |
|---|---|
| Asset (5) | BTC, ETH, BNB, XRP, LINK |
| Year (4) | 2020, 2021, 2022, 2023 |
| Mode (2) | spot (4h), futures (1h) |

Full grid: 5 × 4 × 2 = **40 cells**, none of them inspected.

- **H1** concerns the spot hold-cap only and is judged on its **20 spot
  cells** (5 assets × 4 years × spot).
- **H2** and **H3** are judged on **all 40 cells**.

`scripts/exit_ic.py` takes one `--mode` per invocation, so the 40-cell grid is
produced by two runs per hypothesis (spot + futures), read together. The exact
invocations that constitute the confirmatory run:

```bash
# H1 — spot only, 20 cells
./venv/bin/python scripts/exit_ic.py --mode spot \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --only H1

# H2 — 40 cells (run both modes)
./venv/bin/python scripts/exit_ic.py --mode spot \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --only H2
./venv/bin/python scripts/exit_ic.py --mode futures \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --only H2

# H3 — 40 cells (run both modes)
./venv/bin/python scripts/exit_ic.py --mode spot \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --only H3
./venv/bin/python scripts/exit_ic.py --mode futures \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --only H3
```

`--symbols` and `--years` are spelled out explicitly above even though they
match the script's current defaults — a later default change must not be able
to silently change what this registration certified.

**Denominator is fixed, not "however many cells completed."** If a cell fails
to fetch (network error, exchange rate limit, an unlisted symbol-year), it
must be **retried until it produces a row**, not dropped. A hypothesis is
scored only once every cell in its grid (20 for H1, 40 for H2/H3) has produced
a row. Silently scoring "32 of the 37 that completed" against the ≥32/40
threshold is not scoring against this registration.

---

## 3. Exact rule dicts, as `scripts/exit_ic.py` runs them

Transcribed directly from `scripts/exit_ic.py::main()` at the commit that
introduces this document. A reader can diff this section against that
function to confirm the run matched the registration.

```python
rules = {"baseline": {}}
rules["H1"] = {"max_hold": 72, "exit_params": {"max_position_hours": 288}}
rules["H2"] = {"exit_params": {"trailing_post_tp1_factor": 1.0}}
rules["H3"] = {"exit_params": {"partial_enabled": False}}
```

`"baseline": {}` means `rule.get("exit_params")` is `None`, which
`_simulate_forward` reads as "apply `RISK_CONFIG`/`FUTURES_CONFIG` exactly as
shipped" — zero behaviour change from the running bot. Every hypothesis is
paired against that same baseline, on the same synthetic entry set, within
each cell.

`paired_stats` in `scripts/exit_ic.py` computes `diff = candidate − baseline`
per entry, and `win_share`/`mean_diff` are read off that sign. "Favours the
candidate" below always means `diff > 0` for that pairing — exact ties
(`diff == 0`) do not count toward the candidate.

---

## 4. Shared adoption criteria

Copied verbatim from `docs/superpowers/specs/2026-09-21-exit-mechanics-design.md`,
section 6:

> For a candidate rule to be adopted, **all** must hold:
>
> 1. **Sign consistency** — the paired mean difference favours the candidate in
>    **≥ 80% of that hypothesis's cells**: ≥ 32 of 40 for H2 and H3, ≥ 16 of 20
>    for H1.
> 2. **Effect size** — pooled paired mean improvement **≥ +0.05 percentage points
>    per entry**. Below the per-side slippage assumption (0.05%), an improvement
>    is not actionable.
>
>    **The denominator is ALL entries, not `n_eff`** (pinned before any cell was
>    run). The harness reports `n_eff`, the count of pairs that actually differ,
>    because ties dominate by construction — a rule diverges from baseline only
>    for entries whose baseline trade was still alive at the cap, measured at 22%
>    on the smoke run. `n_eff` is for interpretation and is **not** part of any
>    criterion. An exit rule's practical value is (its effect on the trades it
>    touches) × (how often it touches one), and that product *is* the all-entries
>    mean. Switching denominators after observing that ties dominate would be
>    choosing the goalposts from the results.
> 3. **No mode reversal** — for H2 and H3, the spot subtotal and the futures
>    subtotal must *both* favour the candidate. A candidate that wins overall by
>    winning hugely in one mode and losing in the other is rejected. (Not
>    applicable to H1, which is spot-only.)
>
> A pooled t-statistic is deliberately *not* a criterion: with tens of thousands
> of paired entries any trivial effect clears |t| ≥ 2, so effect size is the
> binding constraint.

### 4.1 Two readings this registration closes off before any cell is run

**"Pooled" means every entry, combined, once — not an average of cell
averages.** Criterion 2's "pooled paired mean improvement" is computed by
concatenating every entry's `diff` across every qualifying cell into one
sample and taking a single mean over that combined sample (i.e. entry-count
weighted). It is **not** the unweighted mean of the 40 (or 20) per-cell
`mean_diff` values — a symbol-year with fewer synthetic entries (e.g. a
shorter or gappier fetch) would otherwise be given the same vote as one with
many more, which is not what "pooled" means and would let a single thin cell
swing the result. Criterion 3's per-mode subtotal follows the same rule: every
entry in that mode's 20 cells, combined once, one mean.

**Sign consistency counts completed, retried-to-completion cells only, against
the fixed denominator in section 2** — never against a shrinking count of
"cells that happened to run."

---

## 5. H1 — `max_hold` unit asymmetry

*Precondition (already satisfied on this branch):* the unreachable-`TIME_EXIT`
defect is fixed, so a position surviving the cap is recorded with a real P&L
instead of being silently discarded as an `OPEN` row. H1 is judged against
that corrected baseline.

*Statement:* expressing the spot hold cap in candles rather than wall-clock
hours — matching futures' 72-candle allowance (`rules["H1"]` above) — improves
mean per-trade net P&L on the same entry population.

*Judged on the shared criteria (§4), restricted to the 20 spot cells, plus one
additional criterion specific to H1:* the share of entries exiting via
`TIME_EXIT` must fall by **≥ 10 percentage points**, and mean per-trade net
P&L must not fall. This is in addition to, not instead of, the three shared
criteria — all four must hold for H1 to be adopted.

---

## 6. H2 — post-TP1 trailing tightening

*Statement, direction fixed before any cell is run:* leaving the trail
unchanged after TP1 (`trailing_post_tp1_factor = 1.0`) improves per-entry net
P&L over the shipped 0.8 tightening. **The candidate arm is `1.0`**
(`rules["H2"]` above); the baseline arm tightens by 0.8 exactly as the bot
ships today.

This document was first committed (`2a39777`) stating H2 the other way round —
"0.8 beats leaving the trail unchanged" — which named the *baseline* as the
thing under test. Criterion 1 reads "favours the candidate", so a pass would
have refuted the stated hypothesis instead of confirming it. The rule dict and
the run commands are **unchanged**; the same numbers are computed either way.
Only the prose direction is corrected, and it is corrected here **before any
confirmatory cell has been run** — the amendment is recorded rather than
silently applied, because a pre-registration that can be edited without a trace
is worth nothing.

This is the identical defect already fixed for H3, and it was caught the same
way: by a reader who was not the author checking the rule dict against the
prose.

**A pass means the post-TP1 tightening does not earn its place. A failure means
the shipped 0.8 stands.**

*Judged on the shared criteria (§4), all 40 cells.*

---

## 7. H3 — partial split at TP1

*Statement, direction fixed before any cell is run:* disabling the 50/50
partial — taking the **whole position at TP2**, with the trail left unchanged
because `trailing_post_tp1_factor` is inert when TP1 never fires — improves
per-entry net P&L. **The candidate arm is `partial_enabled: False`**
(`rules["H3"]` above); the baseline arm takes the 50/50 partial exactly as the
bot ships today.

This is the opposite framing from how H3 was first stated in the design
conversation ("the partial beats a single-target exit"), which would have put
the change in the *baseline* arm and inverted the sign relative to H1 and H2.
`paired_stats` computes `candidate − baseline`, and criterion 1 reads
"favours the candidate", so all three hypotheses must put the change in the
candidate arm or one of three rows in Task 6 is scored with a flipped sign.
The measured quantity is unchanged; only the framing is fixed here, before any
result exists.

**A pass (H3 clears all shared criteria) means the partial does not earn its
place — the 50/50 split at TP1 should be dropped in favour of a single exit at
TP2.**

**A failure (H3 does not clear all shared criteria) means the partial
stands — the 50/50 split at TP1 is kept exactly as it ships today.**

*Judged on the shared criteria (§4), all 40 cells. No additional criterion
beyond §4.*

---

## 8. Closing statement

Failure on any criterion ends that hypothesis. Criteria are not revised after
results are seen.
