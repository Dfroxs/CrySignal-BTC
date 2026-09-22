# Exit mechanics — confirmatory result

**Date scored:** 2026-09-22
**Branch:** `develop`
**Pre-registration:** `docs/superpowers/specs/2026-09-21-exit-prereg.md` (committed
`2a39777`, amended `9f79dc8` / `c6ce5fe` / `7a4728e`, all **before** any
confirmatory cell was run)

**Verdict, all three: FAILED. Nothing ships. `config.py` and `backtest.py` are
unchanged by this result.**

---

## 1. Provenance of the numbers

The five pre-registered invocations (§2 of the pre-registration) were run by
`.superpowers/sdd/2026-09-21-exit-mechanics/run/confirmatory.sh`, which spells
them out verbatim — same `--symbols`, same `--years`, same `--stride 6`, one
`--only` per hypothesis. All five completed 2026-09-22T02:52:02Z, exit 0.

| Log | Hypothesis | Cells | Rows produced |
|---|---|---|---|
| `run/H1-spot.log` | H1 | 20 spot | 20 |
| `run/H2-spot.log` | H2 | 20 spot | 20 |
| `run/H2-futures.log` | H2 | 20 futures | 20 |
| `run/H3-spot.log` | H3 | 20 spot | 20 |
| `run/H3-futures.log` | H3 | 20 futures | 20 |

**Every cell in every grid produced a row.** No cell was retried, dropped, or
scored against a shrunk denominator: H1 is scored on 20 of 20, H2 and H3 on 40
of 40, exactly as §2 fixes. `unresolved` is `0/0` on all 100 rows, so §4.2's
rule (unresolved entries stay in at `0.0`) had nothing to bite on.

The rule dicts in `scripts/exit_ic.py::main()` match §3 character for character:

```python
rules["H1"] = {"max_hold": 72, "exit_params": {"max_position_hours": 288}}
rules["H2"] = {"exit_params": {"trailing_post_tp1_factor": 1.0}}
rules["H3"] = {"exit_params": {"partial_enabled": False}}
```

Each log carries one `[WARNING] binance fetch_ohlcv failed … falling back to
data-api.binance.vision` line. The fallback served every cell; no cell is short
of data because of it.

**Why H1's `n` differs from H2's and H3's.** H1 rows show `n=321/320`; H2 and H3
show `n=330/329`. This is `_tail_margin()` sizing the end-of-frame cushion to the
*longest* rule in the table, baseline included — 72 candles for H1 versus 18 for
H2/H3, so H1 reserves 74 candles of tail and H2/H3 reserve 20. It is a property
of the harness that §2's `--only` invocations pin, not a fetch shortfall. Within
a cell both arms see the identical entry set, which is all the paired comparison
requires.

### How the pooled figures are computed

Entry-weighted, per §4.1: `pooled = Σ(nᵢ · mean_diffᵢ) / Σ nᵢ`, over every cell
in the hypothesis's grid, combined once. Not the unweighted mean of per-cell
means. **The denominator is ALL entries, never `n_eff`** (§4, criterion 2).
`n_eff` appears below for interpretation only and is not part of any criterion.

`mean_diff` is read from the logs at the 4 decimal places the harness prints, so
each pooled figure carries at most ±0.00005 pp of rounding error — three orders
of magnitude below the closest margin that matters below.

---

## 2. H1 — spot hold cap in candles rather than hours

*Candidate:* `max_hold: 72` candles with the hour cap moved to match
(`max_position_hours: 288`). *Baseline:* 18 candles / 72h as shipped.
Judged on the 20 spot cells.

| # | Criterion | Required | Measured | Verdict |
|---|---|---|---|---|
| 1 | Sign consistency — cells with `mean_diff > 0` | ≥ 16 of 20 | **6 of 20** | **FAIL** |
| 2 | Effect size — pooled paired mean, all 6,405 entries | ≥ +0.05 pp | **−0.0957 pp** | **FAIL** |
| 3 | No mode reversal | *not applicable — spot only* | — | n/a |
| 4 | **§5 additional criterion — a single conjunction, both halves required** | H1a **and** H1b | see below | **FAIL** |
| — | *H1a (first half)* — `TIME_EXIT` share must fall | ≥ 10.0 pp | **25.42 pp** (25.47% → 0.05%) | half met |
| — | *H1b (second half)* — mean per-trade net P&L must not fall | pooled `cand_mean ≥ base_mean` | **−0.6310 vs −0.5354 pp** — falls by 0.0957 pp | **half missed** |

**H1a and H1b are not two criteria.** §5 registers them as one conjunction —
the `TIME_EXIT` share must fall by ≥ 10 pp **and** P&L must not fall — so
criterion 4 is satisfied only if both halves hold. The first half is met and the
second is not, which makes **criterion 4 a FAIL**. The 25.42 pp fall is a real
and substantial finding, reported in full below; it is not a criterion that
passed, and nothing in this document should be quoted as though it were.

**Verdict: H1 FAILED** (criteria 1, 2 and 4). The spot hold cap stays at
`MAX_HOLD_CANDLES["4h"] = 18` and `max_position_hours_spot = 72`.

### What the failure says

The mechanic did exactly what it was designed to do and the money got worse.
The first half of criterion 4 is not a near thing — the `TIME_EXIT` share
collapses from 25.47% of entries to 0.05%, clearing a 10-point bar by 15.4
points. Giving spot 72 candles genuinely removes the forced time exit almost
entirely.

Those trades then go on to lose more than the cap was costing. Over the 1,631
pairs that differ at all (25.5% of entries), the mean effect is **−0.3756 pp**:
the positions the cap was cutting short were, on average, positions worth
cutting short. Extending the hold converts a `TIME_EXIT` at a moderate loss into
a trail-stop exit at a larger one.

This is the cleanest result of the three, because it separates the mechanical
claim from the economic one. The unit asymmetry between spot and futures is
real, H1 fixes it, and fixing it loses money on this grid.

### Per-cell arithmetic (20 spot cells)

| Asset | Year | n | n_eff | `mean_diff` | n·`mean_diff` | sign | `time_exit%` base/cand |
|---|---|---:|---:|---:|---:|:-:|---|
| BTC | 2020 | 321 | 85 | +0.1734 | +55.661 | + | 26.5 / 0.3 |
| ETH | 2020 | 321 | 80 | +0.0898 | +28.826 | + | 24.9 / 0.0 |
| BNB | 2020 | 321 | 88 | +0.3682 | +118.192 | + | 27.4 / 0.0 |
| XRP | 2020 | 321 | 76 | −0.2202 | −70.684 | − | 23.7 / 0.0 |
| LINK | 2020 | 321 | 91 | −0.3543 | −113.730 | − | 28.3 / 0.0 |
| BTC | 2021 | 320 | 86 | −0.2480 | −79.360 | − | 26.9 / 0.0 |
| ETH | 2021 | 320 | 84 | −0.0237 | −7.584 | − | 26.2 / 0.0 |
| BNB | 2021 | 320 | 95 | −0.2641 | −84.512 | − | 29.7 / 0.0 |
| XRP | 2021 | 320 | 93 | −0.1845 | −59.040 | − | 29.1 / 0.0 |
| LINK | 2021 | 320 | 79 | −0.0592 | −18.944 | − | 24.7 / 0.0 |
| BTC | 2022 | 320 | 71 | −0.0492 | −15.744 | − | 22.2 / 0.3 |
| ETH | 2022 | 320 | 80 | −0.2838 | −90.816 | − | 25.0 / 0.0 |
| BNB | 2022 | 320 | 92 | −0.2590 | −82.880 | − | 28.7 / 0.0 |
| XRP | 2022 | 320 | 79 | +0.0057 | +1.824 | + | 24.7 / 0.0 |
| LINK | 2022 | 320 | 74 | −0.2554 | −81.728 | − | 23.1 / 0.0 |
| BTC | 2023 | 320 | 70 | −0.0782 | −25.024 | − | 21.9 / 0.0 |
| ETH | 2023 | 320 | 65 | −0.1094 | −35.008 | − | 20.3 / 0.0 |
| BNB | 2023 | 320 | 79 | +0.0102 | +3.264 | + | 24.7 / 0.3 |
| XRP | 2023 | 320 | 85 | +0.0088 | +2.816 | + | 26.6 / 0.0 |
| LINK | 2023 | 320 | 79 | −0.1818 | −58.176 | − | 24.7 / 0.0 |

- Positive cells: **6** (BTC/ETH/BNB 2020, XRP 2022, BNB/XRP 2023). Bar: 16.
- `Σ n = 6,405`; `Σ n·mean_diff = −612.647` → **pooled = −0.095651 pp**.
- Pooled `base_mean = −0.535377 pp`, pooled `cand_mean = −0.631028 pp`
  (same weighting; difference reproduces the pooled `mean_diff` to 6 dp).
- `TIME_EXIT`, entry-weighted: base `Σ n·te_b / Σ n = 25.4655%`,
  cand `= 0.0450%` → fall **25.4205 pp**.

---

## 3. H2 — post-TP1 trailing tightening

*Candidate:* `trailing_post_tp1_factor: 1.0` (leave the trail alone after TP1).
*Baseline:* `0.8` as shipped. Judged on all 40 cells. Per §6, a pass would mean
the shipped tightening does not earn its place.

| # | Criterion | Required | Measured | Verdict |
|---|---|---|---|---|
| 1 | Sign consistency — cells with `mean_diff > 0` | ≥ 32 of 40 | **22 of 40** | **FAIL** |
| 2 | Effect size — pooled paired mean, all 34,880 entries | ≥ +0.05 pp | **+0.0036 pp** | **FAIL** |
| 3 | No mode reversal — both subtotals favour candidate | spot > 0 **and** futures > 0 | spot **+0.0179 pp**, futures **+0.0003 pp** | PASS † |

† See "Open question" in §5. Under the stricter available reading of "favour"
this row is also a FAIL. It changes nothing: H2 has already failed criteria 1
and 2, and failure on any criterion ends the hypothesis.

**Verdict: H2 FAILED.** Per §6, "a failure means the shipped 0.8 stands."
`RISK_CONFIG["trailing_post_tp1_factor"]` stays at `0.8`.

### What the failure says

H2 is not a sign problem so much as a size problem. The pooled effect is
*positive* — leaving the trail alone does very slightly better than tightening
it — but at **+0.0036 pp per entry** it is **13.7× below** the +0.05 pp bar, and
that bar exists precisely because anything under the 0.05% per-side slippage
assumption is not actionable. Criterion 1 then fails outright at 22 of 40.

The two modes behave differently and the split is instructive. On spot, 17 of 20
cells favour the candidate and the subtotal is +0.0179 pp — a consistent, tiny
edge. On futures only 5 of 20 do, and the subtotal is +0.0003 pp, which is zero
in every sense that matters. `n_eff` explains why: only **2.7%** of pairs differ
at all (942 of 34,880), because the post-TP1 factor can only matter on entries
that reach TP1 and are then stopped out on the trail rather than reaching TP2.

One cell, XRP spot 2022, prints `mean_diff=-0.0000pp`. Per §3, exact ties do not
count toward the candidate at either level, and a value that rounds to zero from
below is not positive under any reading; it is scored as not favouring the
candidate. With 22 positives against a bar of 32, this cell cannot change the
verdict either way.

### Per-cell arithmetic

**Spot (20 cells)** — 17 positive, 1 zero-or-below-rounding, 2 negative

| Asset | Year | n | n_eff | `mean_diff` | n·`mean_diff` |
|---|---|---:|---:|---:|---:|
| BTC | 2020 | 330 | 16 | +0.0492 | +16.236 |
| ETH | 2020 | 330 | 13 | +0.0156 | +5.148 |
| BNB | 2020 | 330 | 18 | +0.0198 | +6.534 |
| XRP | 2020 | 330 | 13 | −0.0094 | −3.102 |
| LINK | 2020 | 330 | 17 | +0.0364 | +12.012 |
| BTC | 2021 | 329 | 7 | +0.0380 | +12.502 |
| ETH | 2021 | 329 | 17 | +0.0450 | +14.805 |
| BNB | 2021 | 329 | 13 | +0.0277 | +9.113 |
| XRP | 2021 | 329 | 16 | +0.0327 | +10.758 |
| LINK | 2021 | 329 | 9 | +0.0070 | +2.303 |
| BTC | 2022 | 329 | 3 | +0.0120 | +3.948 |
| ETH | 2022 | 329 | 7 | +0.0047 | +1.546 |
| BNB | 2022 | 329 | 4 | +0.0082 | +2.698 |
| XRP | 2022 | 329 | 6 | −0.0000 | −0.000 |
| LINK | 2022 | 329 | 7 | +0.0166 | +5.461 |
| BTC | 2023 | 329 | 11 | +0.0321 | +10.561 |
| ETH | 2023 | 329 | 7 | +0.0131 | +4.310 |
| BNB | 2023 | 329 | 5 | −0.0065 | −2.139 |
| XRP | 2023 | 329 | 7 | +0.0065 | +2.139 |
| LINK | 2023 | 329 | 8 | +0.0092 | +3.027 |

`Σ n = 6,585`; `Σ n·mean_diff = +117.861` → **spot pooled = +0.017898 pp**
(`n_eff` 204, 3.1%).

**Futures (20 cells)** — 5 positive, 15 negative

| Asset | Year | n | n_eff | `mean_diff` | n·`mean_diff` |
|---|---|---:|---:|---:|---:|
| BTC | 2020 | 1416 | 34 | −0.0008 | −1.133 |
| ETH | 2020 | 1416 | 47 | −0.0055 | −7.788 |
| BNB | 2020 | 1416 | 47 | +0.0030 | +4.248 |
| XRP | 2020 | 1416 | 32 | −0.0001 | −0.142 |
| LINK | 2020 | 1416 | 37 | +0.0071 | +10.054 |
| BTC | 2021 | 1413 | 34 | −0.0052 | −7.348 |
| ETH | 2021 | 1413 | 32 | −0.0029 | −4.098 |
| BNB | 2021 | 1413 | 65 | +0.0251 | +35.466 |
| XRP | 2021 | 1413 | 37 | −0.0048 | −6.782 |
| LINK | 2021 | 1413 | 53 | −0.0002 | −0.283 |
| BTC | 2022 | 1415 | 13 | −0.0004 | −0.566 |
| ETH | 2022 | 1415 | 29 | −0.0025 | −3.538 |
| BNB | 2022 | 1415 | 55 | +0.0013 | +1.839 |
| XRP | 2022 | 1415 | 32 | −0.0048 | −6.792 |
| LINK | 2022 | 1415 | 49 | +0.0078 | +11.037 |
| BTC | 2023 | 1415 | 9 | −0.0006 | −0.849 |
| ETH | 2023 | 1415 | 28 | −0.0017 | −2.405 |
| BNB | 2023 | 1415 | 27 | −0.0015 | −2.123 |
| XRP | 2023 | 1415 | 41 | −0.0049 | −6.933 |
| LINK | 2023 | 1415 | 37 | −0.0018 | −2.547 |

`Σ n = 28,295`; `Σ n·mean_diff = +9.319` → **futures pooled = +0.000329 pp**
(`n_eff` 738, 2.6%).

**Combined:** `Σ n = 34,880`; `Σ n·mean_diff = +127.179` →
**pooled = +0.003646 pp**. Positive cells 17 (spot) + 5 (futures) = **22 of 40**.

---

## 4. H3 — partial split at TP1

*Candidate:* `partial_enabled: False` (whole position at TP2). *Baseline:* the
50/50 partial as shipped. Judged on all 40 cells, shared criteria only.

| # | Criterion | Required | Measured | Verdict |
|---|---|---|---|---|
| 1 | Sign consistency — cells with `mean_diff > 0` | ≥ 32 of 40 | **3 of 40** | **FAIL** |
| 2 | Effect size — pooled paired mean, all 34,880 entries | ≥ +0.05 pp | **−0.0619 pp** | **FAIL** |
| 3 | No mode reversal — both subtotals favour candidate | spot > 0 **and** futures > 0 | spot **−0.0812 pp**, futures **−0.0574 pp** — neither | **FAIL** |

**Verdict: H3 FAILED, on all three criteria.** Per §7, "a failure means the
partial stands — the 50/50 split at TP1 is kept exactly as it ships today."

This is the least ambiguous of the three results: 37 of 40 cells go the wrong
way, **all 20** futures cells go the wrong way, and the pooled effect is
negative rather than merely small.

### §7's registered caveat: this is a three-part bundle

The pre-registration states this before any cell ran, and it constrains what
may be concluded. Setting `partial_enabled: False` changes three things at once:

1. the 50/50 split itself — the whole position exits at TP2;
2. `trailing_post_tp1_factor` goes inert, since TP1 never fires;
3. the **TP1 breakeven snap** never happens — the baseline pulls the trail to
   `entry − 0.5×ATR` (spot) or `entry` (futures) the instant TP1 prints, a floor
   on the remaining 50% that the candidate never gets.

**The failure is evidence about the bundle, not about the split alone.** §7 is
explicit that isolating which of the three drives the effect is out of scope
here, and nothing in this document claims to have isolated it. The third
component is the obvious suspect — review already found a fixture where the
breakeven snap alone flips the outcome from `WIN +4.775` to `TIME_EXIT −7.500` —
but that is a hypothesis for a future pre-registration, not a finding of this one.

### §7.1's registered caveat: the execution-cost asymmetry

`_net_pnl` charges the baseline an effective **1.5 sides** of cost (a 2-sided
partial leg blended 50/50 with a 1-sided remainder) and the candidate the full
**2.0 sides**. From the values pinned in §3.1, that under-charges the baseline by
**0.075 pp** on spot and **0.045 pp** on futures, per entry where the baseline
resolves via the partial path. **The bias runs against H3's candidate.**

§7.1 fixed how to read this before the result existed: a failure "is not made
more suspicious by this asymmetry," and "add back half a side and see if a
near-miss clears" is explicitly not available. `_net_pnl` is not changed, and no
adjusted figure is scored anywhere in this document.

**On computing an add-back at all, directly below a clause that forbids one.**
§7.1's prohibition was read and is honoured. What it forbids is using an
add-back to *rescue* a near-miss — to take a candidate that just missed and
declare it a pass once the bias is removed. The arithmetic below runs in the
candidate's favour and the candidate still fails every criterion by an order of
magnitude, so it can only strengthen the FAIL, never convert it. **No figure
below is scored against any criterion**; the verdict in the table above is
computed entirely from the unadjusted logs. It is reported because a caveat
registered as running against the candidate is worth nothing to a reader unless
its magnitude is shown.

For context only — not as an adjustment, and not as a criterion — the asymmetry
is far too small to be the story.

Over the pairs that actually differ, the candidate is worse by **−0.5762 pp**
(spot) and **−0.6231 pp** (futures) per pair. The asymmetry is 0.075 / 0.045 pp:
roughly **8× and 14× smaller** than the effect it would have to explain.

Zeroing the asymmetry out entirely gives the figures below. Two bases are
available and they differ, so both are given in full rather than one number
taken from each — the pooled figures and the cell counts on a single row are
always computed the same way:

| Basis | Correction applied | spot pooled | futures pooled | combined pooled | cells `> 0` |
|---|---|---:|---:|---:|---:|
| **A** — bias on differing pairs | `bias × n_effᵢ/nᵢ` per cell | −0.0706 pp | −0.0533 pp | −0.0565 pp | **5 of 40** |
| **B** — bias on every entry | full `bias` per cell | −0.0062 pp | −0.0124 pp | −0.0112 pp | **13 of 40** |

Basis **A** is the defensible one: the asymmetry can only bite on entries where
the baseline actually resolved via the partial path, and `n_eff` — pairs whose
arms differ at all — is the largest that count can be. Basis **B** charges the
correction to every entry including the ~86–91% whose arms are identical, so it
over-corrects; it is reported because it is the true arithmetic ceiling, the
most generous number available to the candidate under any reading.

**Neither basis rescues anything.** Criterion 2 needs +0.05 pp and the most
generous basis reaches −0.0112 pp, still 0.061 pp short. Criterion 3 needs both
subtotals positive and both stay negative on both bases. Criterion 1 needs 32 of
40 and the most generous basis reaches 13.

The failure survives its own registered caveat by an order of magnitude.

### Per-cell arithmetic

**Spot (20 cells)** — 3 positive (ETH 2020, BTC 2021, ETH 2021), 17 negative

| Asset | Year | n | n_eff | `mean_diff` | n·`mean_diff` |
|---|---|---:|---:|---:|---:|
| BTC | 2020 | 330 | 53 | −0.0082 | −2.706 |
| ETH | 2020 | 330 | 58 | +0.0107 | +3.531 |
| BNB | 2020 | 330 | 55 | −0.1705 | −56.265 |
| XRP | 2020 | 330 | 39 | −0.0184 | −6.072 |
| LINK | 2020 | 330 | 56 | −0.0781 | −25.773 |
| BTC | 2021 | 329 | 39 | +0.0307 | +10.100 |
| ETH | 2021 | 329 | 44 | +0.0333 | +10.956 |
| BNB | 2021 | 329 | 52 | −0.1248 | −41.059 |
| XRP | 2021 | 329 | 46 | −0.1312 | −43.165 |
| LINK | 2021 | 329 | 50 | −0.2610 | −85.869 |
| BTC | 2022 | 329 | 34 | −0.1098 | −36.124 |
| ETH | 2022 | 329 | 36 | −0.0535 | −17.601 |
| BNB | 2022 | 329 | 35 | −0.0492 | −16.187 |
| XRP | 2022 | 329 | 48 | −0.2511 | −82.612 |
| LINK | 2022 | 329 | 35 | −0.0966 | −31.781 |
| BTC | 2023 | 329 | 56 | −0.0016 | −0.526 |
| ETH | 2023 | 329 | 50 | −0.0765 | −25.168 |
| BNB | 2023 | 329 | 43 | −0.1038 | −34.150 |
| XRP | 2023 | 329 | 46 | −0.1299 | −42.737 |
| LINK | 2023 | 329 | 53 | −0.0349 | −11.482 |

`Σ n = 6,585`; `Σ n·mean_diff = −534.692` → **spot pooled = −0.081198 pp**
(`n_eff` 928, 14.1%).

**Futures (20 cells)** — 0 positive, 20 negative

| Asset | Year | n | n_eff | `mean_diff` | n·`mean_diff` |
|---|---|---:|---:|---:|---:|
| BTC | 2020 | 1416 | 160 | −0.0598 | −84.677 |
| ETH | 2020 | 1416 | 152 | −0.0849 | −120.218 |
| BNB | 2020 | 1416 | 139 | −0.0475 | −67.260 |
| XRP | 2020 | 1416 | 124 | −0.0455 | −64.428 |
| LINK | 2020 | 1416 | 152 | −0.0838 | −118.661 |
| BTC | 2021 | 1413 | 114 | −0.0528 | −74.606 |
| ETH | 2021 | 1413 | 90 | −0.0500 | −70.650 |
| BNB | 2021 | 1413 | 137 | −0.0087 | −12.293 |
| XRP | 2021 | 1413 | 143 | −0.1130 | −159.669 |
| LINK | 2021 | 1413 | 122 | −0.0943 | −133.246 |
| BTC | 2022 | 1415 | 102 | −0.0460 | −65.090 |
| ETH | 2022 | 1415 | 100 | −0.0632 | −89.428 |
| BNB | 2022 | 1415 | 127 | −0.0478 | −67.637 |
| XRP | 2022 | 1415 | 124 | −0.0675 | −95.513 |
| LINK | 2022 | 1415 | 112 | −0.0537 | −75.986 |
| BTC | 2023 | 1415 | 140 | −0.0313 | −44.290 |
| ETH | 2023 | 1415 | 151 | −0.0492 | −69.618 |
| BNB | 2023 | 1415 | 127 | −0.0388 | −54.902 |
| XRP | 2023 | 1415 | 176 | −0.0680 | −96.220 |
| LINK | 2023 | 1415 | 115 | −0.0425 | −60.138 |

`Σ n = 28,295`; `Σ n·mean_diff = −1,624.528` → **futures pooled = −0.057414 pp**
(`n_eff` 2,607, 9.2%).

**Combined:** `Σ n = 34,880`; `Σ n·mean_diff = −2,159.220` →
**pooled = −0.061904 pp**. Positive cells **3 of 40**.

---

## 5. Open question, recorded rather than resolved in the candidate's favour

**What does "favour the candidate" mean in criterion 3?** §4 criterion 3 requires
that "the spot subtotal and the futures subtotal must *both* favour the
candidate." Two readings are available:

- **Sign reading** — the subtotal must be `> 0`.
- **Bar reading** — each subtotal must clear the same +0.05 pp bar criterion 2
  sets.

The pre-registration's own next sentence — "a candidate that wins overall by
winning hugely in one mode and losing in the other is rejected" — describes a
*sign* reversal, so the sign reading is what the document pins, and it is what
§3 of this file scores.

This only ever mattered for H2's futures subtotal, **+0.000329 pp**: positive at
the fourth decimal place and nowhere else, 152× smaller than criterion 2's bar,
and produced by +62.6 of positive cell contributions very nearly cancelling
−53.3 of negative ones. A single cell moving would flip it. It is recorded here
as a PASS by a hair, flagged as such.

Under the less favourable bar reading, H2's criterion 3 is a FAIL as well.
**No verdict in this document changes under either reading**: H2 has already
failed criteria 1 and 2, and H3 fails criterion 3 under both readings.

---

## 6. What ships

**Nothing.** No hypothesis met every applicable criterion, so no value changes.

| Value | Stays at | Would have moved if |
|---|---|---|
| `MAX_HOLD_CANDLES["4h"]` (`backtest.py`) | `18` | H1 had passed |
| `RISK_CONFIG["max_position_hours_spot"]` | `72` | H1 had passed |
| `RISK_CONFIG["trailing_post_tp1_factor"]` | `0.8` | H2 had passed |
| partial split at TP1 (`partial_enabled` default) | enabled | H3 had passed |

`config.py` and `backtest.py` are untouched by this task. `test_pipelines.py`
stands at **100/100**.

The reserve holdout — non-BTC 2024–2025 — **is not read**. §1 makes it available
only if a hypothesis passes its confirmatory grid and a second independent
confirmation is wanted. None did, so it stays untouched and available.

Per `PAPER_RUN.md`, nothing here reaches the running bot. The VPS was not
contacted and no service was restarted.

### The one thing worth carrying forward

H1's split verdict is the substantive finding: the `TIME_EXIT` share falls 25.4
points when spot gets the same 72-candle allowance futures has, and per-trade
P&L gets **worse** by 0.096 pp. The unit asymmetry between the two modes is real
and H1 removes it; removing it costs money on 20 untouched cells. That is a
result about the market, not about the code, and it is the kind of thing this
grid exists to find.

It is not a licence to test "what about 36 candles?" Every criterion above was
fixed before any cell ran, and picking a new cap after seeing that 72 fails is
the exact move the pre-registration exists to prevent.
