# Exit mechanics — design

**Date:** 2026-09-21
**Branch:** `develop`
**Status:** design approved in conversation; implementation plan not yet written

---

## 1. Why this exists

The 2026-08-30 paper run produced three closed spot trades in 21.5 days and zero
futures trades. Reviewing it raised a request to improve P&L.

`CHANGELOG.md` "STEP 1 CLOSED" already tested and rejected the premise that any
scoring component predicts direction: 17 conditions across 29 cells, 8 assets,
7 years, nothing held. `CLAUDE.md` therefore forbids retuning weights,
thresholds and gates.

STEP 1 tested **entry prediction** — does condition *X* predict forward return.
It tested nothing about **how a position is closed**. That gap is real, and it
is where this work sits.

### The specific opening

Of the three trades in the live run, **two exited via `MACRO_CLOSE`** — a forced
close ahead of a HIGH-impact USD event, not the strategy's own TP, SL or
trailing logic. The strategy's exit path was exercised exactly once. Whatever
the exit rules do, almost nothing in the record measures it.

---

## 2. Findings that motivate the design

Measured during design (`backtest.py --costs`, BTC, both modes):

| Run | Signals | Closed | Stranded at `max_hold` | P&L | Cost share of gross |
|---|---|---|---|---|---|
| spot 2025 | 4 | 2 | **2** | +1.37% | 28% |
| spot 2024 | 3 | 2 | **1** | −4.79% | 14% |
| futures 2025 | 3 | 3 | 0 | −1.51% | 56% |
| futures 2024 | 7 | 7 | 0 | +0.99% | **54%** |

Three things follow.

**Execution cost is heavy but not fatal.** 28–56% of gross. Exit work can still
matter; it must clear a high bar to be more than noise.

**`max_hold` carries a unit mismatch.** `RISK_CONFIG["max_position_hours_spot"]`
and `["max_position_hours"]` are both 72. On spot 4H that is **18 candles**; on
futures 1H it is **72 candles** — a 4× asymmetry that is an artifact of
expressing a candle-count limit in wall-clock hours. The comment at
`config.py:43` claims "enough room for swing to develop"; the measurement
contradicts it.

**The `TIME_EXIT` branch is unreachable in both modes.** Discovered while
planning, and it is the more serious of the two. `_simulate_forward` iterates
`range(entry_idx + 1, entry_idx + 1 + max_hold)`, so `age` runs `1..max_hold`.
The time-exit test is `age * mult > max_hours`:

| mode | tf | loop gives age | `max_hold × mult` | cap | fires at |
|---|---|---|---|---|---|
| spot | 4h | 1..18 | 72h | 72h | **never** |
| futures | 1h | 1..72 | 72h | 72h | **never** |

The loop bound and the time cap express the same duration, and the loop is
checked first with an exclusive bound. Every position still alive at the cap
falls through to the `OPEN` row at `pnl = 0`, and `RESOLVED` excludes `OPEN`
from every statistic. **The backtest does not measure slow trades — it discards
them.** Measured consequence: one to two of every three or four spot trades
vanishes from the sample.

This is a defect in the measuring instrument, not a parameter choice. It must be
fixed before any hypothesis is judged, because it moves the baseline every
hypothesis is measured against.

**The whole test population is 14 closed trades** across two years and two
modes. No exit hypothesis can be judged on that.

### One item removed from the backlog

`PAPER_RUN.md` item 3 and the corresponding note in `CLAUDE.md` say the
ForexFactory calendar timezone was "never checked". Both are **stale**.
`signals/sentiment.py:_parse_macro_timestamp` documents a verification against
five fixed-time releases and parses the feed as UTC. Verified again against the
live feed during design: Unemployment Claims on 2026-09-24 publishes as
`12:30pm`, which is 08:30 ET + 4 (EDT). The feed is UTC and the parser is
correct. Both documents need correcting; no work is owed here.

---

## 3. Approach

Exit hypotheses cannot be judged on 14 trades. The repository has already
solved this exact problem once, for the entry side: `scripts/condition_ic.py`
exists because, per `CLAUDE.md`, "the full system fires ~12–15 times a year and
can never be validated on its own trade count; its components are evaluated
every candle."

This design applies that pattern to the exit side.

**The question "does trailing to breakeven after TP1 beat a flat 2.5R target?"
is a property of price behaviour, not of the entry signal.** Measuring it does
not require entries with an edge — it requires many entry points. Take every
Nth candle in a window as a hypothetical entry, run each candidate exit rule
forward from that same point, and compare. The sample moves from 14 to tens of
thousands.

This also separates cleanly from STEP 1. STEP 1 closed "does condition *X*
predict direction". This asks "given that a position is open, which exit rule
retains more of the move that exists". Untouched by that result.

### What this cannot do

Synthetic entries have no edge, so the harness reports **relative** comparisons
between exit rules on an identical price population. It cannot predict absolute
P&L, and it cannot promise P&L will rise. It is sufficient to choose between
rules; it is not sufficient to promise profit.

---

## 4. Components

### 4.1 Parameter injection into the existing simulator

```python
_simulate_forward(df, entry_idx, signal, max_hold, timeframe, mode, exit_params=None)
```

`exit_params=None` reads `RISK_CONFIG` / `FUTURES_CONFIG` exactly as today —
**zero behaviour change**. A dict overrides only the named knobs.

Knobs `_simulate_forward` currently reads from module globals, and which
therefore become injectable:

| Knob | Source today |
|---|---|
| `trailing_atr_factor` | `FUTURES_CONFIG` (futures) / `RISK_CONFIG` (spot) |
| `trailing_post_tp1_factor` | `RISK_CONFIG` |
| `trailing_advance_min_ratio` | `RISK_CONFIG` |
| `max_position_hours` / `max_position_hours_spot` | `RISK_CONFIG` |
| `vol_expansion_exit_mult` | `RISK_CONFIG` |

**Why injection and not a second simulator.** `generate_signals()` already
takes `threshold_override` and `disabled` for the same reason: `CLAUDE.md`
records that ablation "cannot drift from the real scoring path" precisely
because it runs the real path rather than a copy. A second exit simulator would
drift from `_simulate_forward` within months and nothing would report it.

### 4.2 `scripts/exit_ic.py`

Mirrors `condition_ic.py` in shape and CLI (`--start/--end`, `--matrix`,
`--only`, `--symbol(s)`, `--years`).

| Stage | Behaviour |
|---|---|
| Entry generation | every **6th** candle in the window is a hypothetical **BUY** (`--stride`, default 6); `entry = close`, `SL = entry − atr_multiplier × ATR`, `TP1` from `take_profit_rr`, `TP2 = 2 × TP1 distance` — the same formulas the live path uses |
| Execution | `_simulate_forward()` re-run over the **identical entry set** for each candidate rule |
| Reporting | **paired** statistics — per-entry differences, not two independent samples |

**The paired design is the statistical point.** Every rule sees the same
entries, so a difference cannot be confounded by *which trades were taken*.
That confound is exactly what makes threshold sweeps in this repository
unreadable — `CLAUDE.md` records that `open_until` makes each threshold sample a
different sequence. Here the sequence is held fixed, so the difference is
readable.

**Entries are BUY-only.** Spot cannot short, so a mixed population would not be
comparable across modes. SELL-side exits are a possible later extension, not
part of this work.

**Stride and autocorrelation.** A stride of 6 candles leaves entries whose
forward windows still overlap, so entries are *not* independent draws. This is
tolerable here because the comparison is paired — both rules inherit the same
overlap — and because the adoption criteria are cell-level sign consistency and
effect size, neither of which assumes independence. It would *not* be tolerable
for a t-test, which is one more reason none is used.

Data source: `_fetch_ohlcv_range()` from `backtest.py`, which already pages
around the exchange's 1000-bar cap. No HTF is needed — exit rules read price
and ATR only.

### 4.3 Pre-registration document

Written and committed **before** the confirmatory run, carrying hypotheses,
cells, and numeric pass/fail criteria. Format follows the precedent in
`CHANGELOG.md` STEP 1.

---

## 5. Data hygiene

BTC spot and futures for **2024 and 2025** were inspected during this design
(section 2). Those cells are **exploratory and now burned** — testing a
hypothesis on the data that generated it proves nothing.

**A cell is one (asset, year, mode) triple.** Confirmatory grid: 5 assets
(BTC, ETH, BNB, XRP, LINK) × 4 years (2020, 2021, 2022, 2023) × 2 modes
(spot 4H, futures 1H) = **40 cells**, none of them inspected.

H1 concerns the spot hold cap only, so it is judged on the **20 spot cells**.
H2 and H3 are judged on all 40.

**Reserve holdout:** non-BTC 2024–2025, untouched, not to be read unless a
hypothesis passes and a second independent confirmation is wanted.

---

## 6. Pre-registered hypotheses

Criteria are fixed here, before the confirmatory run. **Failure on any
criterion ends that hypothesis.** Goalposts do not move after results are seen —
that error has already been made twice in this project with `htf`.

Each hypothesis is judged with `--only`, reading one committed row, which is
what keeps three hypotheses from becoming a multiple-comparison search.

### Shared criteria

For a candidate rule to be adopted, **all** must hold:

1. **Sign consistency** — the paired mean difference favours the candidate in
   **≥ 80% of that hypothesis's cells**: ≥ 32 of 40 for H2 and H3, ≥ 16 of 20
   for H1.
2. **Effect size** — pooled paired mean improvement **≥ +0.05 percentage points
   per entry**. Below the per-side slippage assumption (0.05%), an improvement
   is not actionable.

   **The denominator is ALL entries, not `n_eff`** (pinned before any cell was
   run). The harness reports `n_eff`, the count of pairs that actually differ,
   because ties dominate by construction — a rule diverges from baseline only
   for entries whose baseline trade was still alive at the cap, measured at 22%
   on the smoke run. `n_eff` is for interpretation and is **not** part of any
   criterion. An exit rule's practical value is (its effect on the trades it
   touches) × (how often it touches one), and that product *is* the all-entries
   mean. Switching denominators after observing that ties dominate would be
   choosing the goalposts from the results.
3. **No mode reversal** — for H2 and H3, the spot subtotal and the futures
   subtotal must *both* favour the candidate. A candidate that wins overall by
   winning hugely in one mode and losing in the other is rejected. (Not
   applicable to H1, which is spot-only.)

A pooled t-statistic is deliberately *not* a criterion: with tens of thousands
of paired entries any trivial effect clears |t| ≥ 2, so effect size is the
binding constraint.

### H1 — `max_hold` unit asymmetry

*Precondition:* the unreachable-`TIME_EXIT` defect (section 2) is fixed first, so
that positions surviving the cap are recorded with a real P&L instead of being
discarded. H1 is judged against that corrected baseline, never against the
current one.

*Statement:* expressing the spot hold cap in candles rather than wall-clock
hours — matching futures' 72-candle allowance — improves mean per-trade net P&L
on the same entry population.

*Additional criterion:* the share of entries exiting via `TIME_EXIT` must fall
by **≥ 10 percentage points**, and mean per-trade net P&L must not fall.

### H2 — post-TP1 trailing tightening

*Statement:* `trailing_post_tp1_factor = 0.8` (tighten the trail 20% after TP1)
beats leaving the trail unchanged, on the same entry population.

*Judged on the shared criteria.*

### H3 — partial split at TP1

*Statement (restated for sign uniformity, before any cell was run):* disabling
the 50/50 partial — taking the whole position at **TP2**, with the trail left
unchanged because `trailing_post_tp1_factor` is inert when TP1 never fires —
improves per-entry net P&L.

The spec originally stated this the other way round ("the partial beats a
single-target exit"), which puts the change in the *baseline* arm and inverts
the sign relative to H1 and H2. `paired_stats` computes `cand - base`, and the
shared criterion reads "favours the candidate", so all three hypotheses must
put the change in the candidate arm or Task 6 scores one of three rows with a
flipped sign. The measured quantity is unchanged; only the framing is unified.

**A pass means the partial does not earn its place. A failure means the partial
stands.**

*Judged on the shared criteria.*

---

## 7. Testing plan

1. **Golden regression test.** `exit_params=None` reproduces today's trades
   exactly over a fixed window — same outcomes, same exit indices, same P&L.
   This is what keeps the injection from silently altering `backtest.py`.
   It is recorded *before* the injection lands and must pass on both sides of
   it. The `TIME_EXIT` fix is a **deliberate** behaviour change and is made in a
   separate commit afterwards, with its own before/after record, so the two
   never mix.
2. **Per-knob unit tests.** Each injected parameter changes behaviour in the
   expected direction, and an unknown key is rejected rather than ignored.
3. **Harness determinism test.** The same window and seed produce the same entry
   set and the same paired output.
4. **Confirmatory run.** Pre-registered hypotheses on the 20 untouched cells;
   criteria read as written.

---

## 8. Out of scope

| Excluded | Reason |
|---|---|
| Thresholds, the 1.2× confidence multiplier, condition weights | `CLAUDE.md` forbids retuning; STEP 1 closed the entry side |
| Repairing the inert adaptive controller | different goal (sample size, not P&L); mixing it in makes both results unreadable |
| Macro force-close policy | needs a historical HIGH-impact USD calendar, which the weekly ForexFactory feed does not provide; held behind a feasibility spike |
| Anything applied to the running bot | `PAPER_RUN.md`: change nothing while the run is live; findings apply to the next run |

---

## 9. What a null result means

If all three hypotheses fail their own criteria, **nothing ships**, and that is
recorded in `CHANGELOG.md` the way STEP 1 was. The harness and the injection
still stand — they are the instrument, and a better-evidenced attempt can reuse
them.

Given that execution cost already consumes 54% of gross on futures, a null
result is a realistic outcome and not a failure of the process.
