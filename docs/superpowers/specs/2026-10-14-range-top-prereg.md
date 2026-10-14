# Pre-registration: do spot BUYs at the top of their recent range lose? (H-RT)

**Written 2026-10-14, before the instrument exists and before any figure below has been
computed.**

## Why

Run 3's three spot losses (positions #5, #6, #7, 2026-10-11 to 10-14) were one episode.
BTC chopped between $81.0k and $84.4k, and all three entries were bought in the upper
part of that range. Forensics on a pulled copy of the server database and Binance 1H
candles found:

| | #5 | #6 (pyramid on #5) | #7 |
|---|---|---|---|
| entry | 83,164 | 83,773 | 83,435 |
| position in the prior 72h range | 90% | 74% | 72% |
| TP1 distance | +3.75 ATR | +3.12 ATR | +3.75 ATR |
| best excursion before exit | +2.15 ATR | +1.00 ATR | +0.86 ATR |
| net P&L | −1.06% | −0.76% (half size) | −0.34% (MACRO_CLOSE) |

All three cited "BB squeeze + above middle — breakout coil" and a VWAP cross-up. In all
three, 1D MACD was bearish. #5 and #6 also had StochRSI above 90 and volume at 0.4–0.5×
its average. TP1 lay above the range's high every time, so it could only be reached on
a breakout, and none came. The exits did their job: the trail saved #5 about 0.9pp
against its original stop, and MACRO_CLOSE saved #7 about 1.3pp ahead of the
14 Oct 12–16h drop.

That is three trades in one week and one regime, so it is **a lead, not a finding**. The
cut-off below (70%) was chosen after seeing those three trades. That is acceptable only
because the test runs on data that excludes them: the cache ends 2026-08-30, six weeks
before the first of them. The cut-off is fixed now and will not be swept.

## Data (fixed now)

Identical to H-SR/H-PSY (`2026-10-10-sr-psy-gates-prereg.md`), so that its `kept` arm
can be reproduced as the fidelity check:

- The sibling project's hash-verified OKX 4h cache through `scripts/entry_ic.py`'s
  `load_symbol`, on its 10 `TUNING` symbols (BTC, ETH, SOL, BNB, OKB, ICP, SUSHI, NEAR,
  UNI, DOGE). Nakhoda's locked market-holdout symbols are not read.
- HTF (1D, 1W) resampled from the 4h base (`entry_ic.build_htf`, strict warmup).
- Scored as live scores it: `backtest._score_candle`, `mode='spot'`, fixed
  `SPOT_THRESHOLD`, `disabled=None` (so `config.VETOES_DISABLED` applies).
- **Primary window:** from each symbol's warmup to 2025-08-30. **Confirmation:**
  2025-08-30 → 2026-08-30.

## Definitions (fixed now)

On the signal bar `i`, using only bars up to and including `i`:

- **Range position** `p = (close_i − L) / (H − L)`, where `H` and `L` are the highest
  high and lowest low of the 18 bars ending at `i` (72 hours of 4h bars). If `H == L`,
  the bar is excluded and counted.
- **Range top:** `p ≥ 0.70`.
- **Squeeze:** the engine's own BB condition emitted "BB squeeze + above middle",
  read as a positive `buy_delta` in `signal['_contributions']['bollinger']` with the
  bar's close above `BB_Middle`. The instrument reads that, not a reimplementation.
- **Low volume:** `volume_i / mean(volume over the 20 bars ending at i) < 0.7`, the
  engine's own "Low volume" cut-off (`signals/engine.py`, condition 4).

## Universe

The H-SR `kept` arm: engine BUYs with confidence NORMAL or STRONG that fail **no** gate
under `spot_gates_ic.gates_for` (with `confidence_first` and `reentry_first` ignored, as
there). These are the entries live would open, so a split of them is what a new gate
would act on. They are **de-duplicated first**, exactly as H-SR's `kept` is (no new
entry while that symbol's previous kept entry is open), and **split afterwards**, so the
filter is judged on the trades live would actually have taken. Each trade goes through
`backtest._simulate_forward` in spot mode (live spot exit and costs, max hold 18 bars).
OPEN rows are excluded and counted.

## Hypotheses

**H-RT1, range top alone.** Split the universe on range top. d = mean(not top) −
mean(top), with a 90% bootstrap interval (`regime_ic.diff_ci`: 5,000 resamples, seed 2026,
each arm resampled independently).
- **PASS (range-top BUYs are worse)** if d > 0 **and** the interval lies above 0.
- **FAIL (no evidence they are worse)** if d ≤ 0.
- **INCONCLUSIVE** otherwise.

**H-RT2, the compound pattern.** Same rule, with flagged = range top **and** squeeze
**and** low volume (#5 and #6's profile), against every other trade in the universe.

**H-RT3, descriptive.** For each flagged set: the engine's flagged trades against
count-matched random bars where the same flag holds (20 seeds, drawn per symbol in
proportion, excluding the flagged bars, levels from `exit_ic._signal_at`). It answers
whether the engine adds anything at the range top. Reported only. It cannot overturn
H-RT1 or H-RT2.

**Descriptive, both sets.** TP1-hit rate, the share of trades whose TP1 lies above `H`,
and mean best excursion in ATR, for flagged against unflagged. BTC-only figures, since
live trades BTC only. A table by range-position quintile, labelled **not
verdict-bearing**. No cut-off is chosen from it.

**Across windows** (per hypothesis): PASS only if both windows PASS. FAIL if the primary
FAILS and the confirmation does not PASS. Otherwise INCONCLUSIVE.

**Power guard:** fewer than 30 flagged trades in the primary, or fewer than 10 in the
confirmation, makes that window INCONCLUSIVE. Every arm's count is reported, including
the unflagged one.

Two hypotheses at 90% each with no multiple-comparison correction means that one false
PASS in two is not unlikely, and must be read that way. H-RT2 is a subset of H-RT1's
flagged set, so the two are not independent either.

## Discard

- **Fidelity:** the pooled universe must reproduce H-SR's `kept` arm, n = 347 primary
  and n = 43 confirmation with the same means (−0.165pp, −0.515pp to 3 dp). No file the
  universe depends on has changed since H-SR's instrument (`571b024`): `git log
  571b024..f419885` over `signals/`, `backtest.py`, `config.py` and the reused scripts
  is empty. Any difference → DISCARDED.
- The squeeze flag fires on no trade in the universe → the instrument does not read
  the condition → DISCARDED.

## What each verdict licenses

Run 3's clause discards the run on any change to Phase 3 logic or `config.py` on the
server. So no verdict here changes the live bot during run 3 by itself.

- **PASS (H-RT1 or H-RT2):** a run-4 candidate gate, "no first spot BUY in the top 30%
  of the 72h range" (or the compound version), entered in `run4-backlog.md` as a
  tested change. The owner may also ship it mid-run, which restarts run 3's window like
  the 2026-10-09 restarts. It must not be applied to futures without its own test.
- **FAIL or INCONCLUSIVE:** not licensed. The episode is recorded as one losing week in
  a chop range, and nothing changes. A second range-position cut-off may not be tried on
  these windows: that would be the sweep this document forbids.
- Whatever the verdict, TP1 placement (above the range's high) is **not** tested here.
  Spot exit tweaks failed on 09-21, and a TP1 rule needs its own pre-registration.

## Instrument

`scripts/range_top_ic.py`, reusing `spot_gates_ic`'s scoring, gate and simulation code,
committed before the scored run, with a smoke run that produces counts only (first 400
candles of BTC). Raw output goes to `2026-10-14-range-top-run/`.
