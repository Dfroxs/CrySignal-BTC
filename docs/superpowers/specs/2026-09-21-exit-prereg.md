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
    --years 2020,2021,2022,2023 --stride 6 --only H1

# H2 — 40 cells (run both modes)
./venv/bin/python scripts/exit_ic.py --mode spot \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --stride 6 --only H2
./venv/bin/python scripts/exit_ic.py --mode futures \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --stride 6 --only H2

# H3 — 40 cells (run both modes)
./venv/bin/python scripts/exit_ic.py --mode spot \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --stride 6 --only H3
./venv/bin/python scripts/exit_ic.py --mode futures \
    --symbols BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT \
    --years 2020,2021,2022,2023 --stride 6 --only H3
```

`--symbols`, `--years`, and `--stride` are spelled out explicitly above even
though they all match the script's current defaults — a later default change
must not be able to silently change what this registration certified.
`--stride` is pinned deliberately: it is the single largest lever on entry
count, and therefore on `n`, on the entry-weighted pooled mean §4.1 pins, and
on `n_eff`.

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
**per entry**; `mean_diff` is the average of those per-entry `diff` values
**over one cell**, and is the field `main()` prints per row. Criterion 1
("favours the candidate") ranges over *cells*, not entries, and is evaluated
on `mean_diff`: a cell counts toward the ≥ 32/40 (or ≥ 16/20) tally when that
cell's own `mean_diff > 0`. `win_share` (the share of *entries* with
`diff > 0`) is printed for interpretation next to `mean_diff` and is easy to
mistake for the criterion — it is not one. Exact ties (`diff == 0` at the
entry level, `mean_diff == 0` at the cell level) do not count toward the
candidate at either level.

### 3.1 The baseline, pinned numerically

"Exactly as shipped" is pinned to the values in `config.py` at commit
`9f79dc8` (the tip of `develop` when this fix round was written; this round
does not touch `config.py` or `backtest.py`'s exit logic, so these are also
the values at the commit that introduces this section). A reader can run
`git show 9f79dc8:config.py` and confirm every number below directly, rather
than trusting the prose.

The values every hypothesis moves against:

| Key | Value | Read by |
|---|---|---|
| `RISK_CONFIG["atr_multiplier"]` | `1.5` | `_signal_at` — sets every synthetic SL |
| `RISK_CONFIG["take_profit_rr"]` | `2.5` | `_signal_at` — sets every synthetic TP1 (TP2 = 2× the TP1 distance) |
| `RISK_CONFIG["trailing_atr_factor"]` | `2.0` (spot) | `_simulate_forward`'s base trail width |
| `FUTURES_CONFIG["trailing_atr_factor"]` | `1.5` | same, futures |
| `RISK_CONFIG["trailing_advance_min_ratio"]` | `0.5` | minimum trail advance before it moves |
| `RISK_CONFIG["trailing_post_tp1_factor"]` | `0.8` | H2's baseline value — tighten 20% after TP1 |
| `RISK_CONFIG["max_position_hours_spot"]` | `72` (→ **18 candles** at 4h) | **the exact quantity H1 tests** — `MAX_HOLD_CANDLES["4h"] = 18`, `18 × 4h = 72h` |
| `RISK_CONFIG["max_position_hours"]` | `72` (→ **72 candles** at 1h, futures) | the asymmetry H1's statement is about |
| `RISK_CONFIG["vol_expansion_exit_mult"]` | `2.0` | vol-exit gate |
| `EXECUTION_CONFIG["spot_fee_pct"]` / `["slippage_pct"]` | `0.10` / `0.05` | `_costs`, spot |
| `EXECUTION_CONFIG["futures_fee_pct"]` / `["slippage_pct"]` | `0.04` / `0.05` | `_costs`, futures |

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

### 4.2 Unresolved entries — the rule, fixed now

`run_rule` (`scripts/exit_ic.py`) enters an OPEN row or the zero-ATR guard
into the sample as `0.0` and separately counts it in `unresolved_base` /
`unresolved_cand`, printed per row. **That count is never grounds to drop a
cell, exclude an entry, or reweight a hypothesis's verdict — at any
observed unresolved share, in any cell.** There is no threshold.

This is the same rule already pinned for `n_eff` (§4, criterion 2): an arm
that fails to resolve often is an arm whose recorded improvement is diluted
by exactly that many ties at `0.0`, and the all-entries effect-size
denominator already prices that dilution in. A large unresolved share is
information for reading a result, exactly like `n_eff` — never a reason to
discard the result. Deciding otherwise after seeing which cells have large
unresolved shares would be choosing the goalposts from the results, which is
the one thing this document exists to prevent.

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
P&L must not fall. This is in addition to, not instead of, the shared
criteria — **all applicable criteria (1, 2, and the additional one) must
hold** for H1 to be adopted. Criterion 3 (no mode reversal) does not apply:
H1 is spot-only, so there is no futures subtotal to reverse against (§4,
criterion 3).

**Which printed fields the additional criterion is read from.** `run_rule`
(`scripts/exit_ic.py`) now counts `TIME_EXIT` outcomes per arm on the `Pnls`
object it returns (`.time_exit`, alongside the existing `.unresolved`).
`run_cell` divides each arm's count by that cell's entry count
(`len(entries)`, the same all-entries denominator §4.1 pins for effect size)
and `main()` prints both as `time_exit_pct_base` / `time_exit_pct_cand` on
every row (`time_exit%=base/cand` in the printed output). The "≥ 10
percentage point fall" is `time_exit_pct_base − time_exit_pct_cand`, pooled
the same entry-weighted way §4.1 requires for criterion 2, over the 20 spot
cells. "Mean per-trade net P&L must not fall" is read from the same rows'
`cand_mean ≥ base_mean`, pooled identically.

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

**H3 is a three-part bundle, not a single change — registered honestly here
before any cell runs.** Disabling `partial_enabled` changes three things at
once, not one:

1. The 50/50 split itself — the whole position exits at TP2 instead of half
   at TP1 and half at TP2.
2. `trailing_post_tp1_factor` goes inert (TP1 never fires, so the post-TP1
   tighten never applies) — already stated above.
3. **The TP1 breakeven snap never happens.** In the baseline arm, the TP1
   block also pulls the trail to `entry − 0.5×ATR` (spot) or `entry`
   (futures) the instant TP1 prints — locking in a floor on the remaining
   50% that the candidate arm never gets, because it never takes TP1 at all.
   On a fixture where price prints TP1's high and then reverses without
   reaching TP2, this changes the outcome by itself: `partial ON` closes WIN
   at the breakeven-snapped trail (`exit 950.0, pnl +4.775`), `partial OFF`
   has no snap to fall back on and runs into `TIME_EXIT` deep underwater
   (`exit 928.0, pnl −7.500`) — found during review, on `_exit_fixture`.

**A pass or a failure on H3 cannot be attributed to the partial split alone.**
It is evidence about the three-part bundle — split, post-TP1 tighten, and
breakeven snap — together. Isolating which of the three drives any observed
effect is out of scope for this registration and is not claimed here.

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

### 7.1 Execution-cost asymmetry between H3's arms — registered, not corrected

`_net_pnl` (`backtest.py`) charges cost as `_costs(mode, sides)`, `sides` = 1
when a partial was taken, 2 when it wasn't. The baseline arm blends a
2-sided-cost partial leg with a 1-sided-cost remainder 50/50, so it pays an
**effective 1.5 sides** of cost overall; the candidate arm (`partial_enabled:
False`) always takes the `partial_closed=False` branch and pays the full
**2.0 sides**. This is exactly how `trading/paper.py` and the live bot price
a partial fill, and mirroring it is deliberate (§4.1 of the design doc, and
Task 5's own report).

**`_net_pnl` is not being changed.** It is the real cost model the running
bot uses; altering it mid-experiment would move the baseline every hypothesis
in this document is judged against, for the second time on this branch (the
first was the `TIME_EXIT` fix, already isolated in its own commit).

**Magnitude, computed from the pinned values in §3.1:** the baseline is
under-charged relative to the candidate by 0.5 side-equivalents of
`fee + slippage`:

- Spot: `0.5 × (0.10 + 0.05) = 0.075` percentage points.
- Futures: `0.5 × (0.04 + 0.05) = 0.045` percentage points.

**Direction: this biases against H3's candidate.** The candidate is charged
more in modeled cost than the baseline, on every entry where the baseline
resolves via the partial path. Criterion 2's bar is `+0.05` percentage
points, so this asymmetry alone is large enough to account for a marginal
failure on spot cells.

**How H3's verdict is read in light of this, decided now:**

- A **failure** is not made more suspicious by this asymmetry — it may
  simply be real cost, not evidence the candidate is worse in a way that
  would reverse if execution were modeled differently.
- A **pass**, arrived at despite a bias running against the candidate, is
  **stronger** evidence than an equivalent pass would be without the
  asymmetry.
- This is not a threshold, an adjustment, or a reason to re-run with
  different costs. It is context for reading whichever result appears, fixed
  before that result exists. "Add back half a side and see if a near-miss
  clears" is explicitly not an action available after Task 6 runs.

---

## 8. Closing statement

Failure on any criterion ends that hypothesis. Criteria are not revised after
results are seen.
