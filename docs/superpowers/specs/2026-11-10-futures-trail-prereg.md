# Pre-registration: is futures' trail too wide? (H-TRL)

**Written 2026-11-10, before the instrument exists and before any figure below has been
computed.** Companion to `2026-11-10-futures-tp1-prereg.md` (H-TPF), which ended FAIL
(`2026-11-10-futures-tp1-results.md`). It reuses H-TPF's design, arms and discard rules.

## Why

Run 3's futures book, snapshot 2026-11-09 (the same observation H-TPF started from):

- Two winners gave their move back before closing: #13 peaked **+3.48%** and closed
  **+0.18%**; #4 peaked **+1.67%** and closed **+0.19%**. #15 was **+0.80%** in profit
  at its best and closed **-1.61%**.
- No position reached TP1, so the post-TP1 trail tightening (x0.8) never applied. Every
  live exit so far was governed by the full 3.5 x ATR trail, a stop, or a flip.

n = 7. A reason to look, not a result.

## The prior is against this candidate, and it is stated here

`2026-10-09-futures-exit-prereg.md` found that **widening** the trail from 1.5 to 3.5 (with
stop x2) **PASSED**: +0.243pp, 4 of 4 windows, both directions, on BTC 2022-01 to
2025-10. That prereg also records that a descriptive grid of six exit settings improved
**monotonically as the trail widened**. So the evidence on file points the other way
from the observation above. H-TPF's synthetic arms also lost most in strongly trending
years, where a wide exit lets winners run.

This test is still worth registering for three reasons: that PASS was **BTC only**, the
grid behind it was **one year**, and the observation is about give-back, which a trail
tightened toward the middle of the tested range might reduce without returning to 1.5. A
FAIL here is the expected outcome and is informative: it would put a floor under how far
the trail can come in.

## The one candidate (fixed now)

**`trailing_atr_factor` = 2.5** (futures; live is 3.5). It is the midpoint of the two
values already tested (1.5 and 3.5), so the result either extends the earlier curve or
shows it has a peak in between. Everything else stays exactly as live: stop and target
distances (x2), the initial stop, TP1/TP2 (H-TPF left them alone), the 50% partial, the
post-TP1 factor (0.8), `trailing_advance_min_ratio` (0.5), the 72h hold cap, the
volatility exit, the funding exit and costs.

Applied through the simulator's existing `exit_params={"trailing_atr_factor": 2.5}`, the
route `trail_ic.py` used. No second simulator, no live code change for the test.

**One candidate only: no other trail value (2.0, 3.0, ...) is run or reported.**

**Not tested here:** a trail that tightens only after a given profit (a profit lock). It
is the structure that would fit the give-back observation best, but it needs a new
simulator knob and a live code change, so it cannot be tested "through the real
simulator" without writing that. It is deferred and may get its own pre-registration
once its knob exists.

## Contamination, disclosed

1. The candidate lies between values the 10-09 test already ran on BTC 2022-01 to
   2025-10. **Arm C primary (2022-01 to 2025-10) is that window**: a guard, not evidence.
2. Arm B's BTC 2022 and 2023 cells overlap that window. The other four assets in Arm B,
   and all of Arm A, are fresh to trail tests. Ex-BTC figures are reported (never a
   criterion).
3. Arm C confirmation ends **2026-10-09**, before run 3 began; no live position overlaps
   it. The seven live positions are the observation that suggested this and are never an
   arm.

## Arms

Identical to H-TPF: `backtest._simulate_forward`, futures mode, 1h, max hold 72 bars,
live futures costs, live geometry (`_exit_signal`), each entry run under both settings,
paired `diff = pnl(candidate) - pnl(baseline)`.

- **A** synthetic BUY entries (`exit_ic` method, stride 24): BTC, ETH, BNB, XRP, LINK,
  **2024 and 2025**: 10 cells.
- **B** same, **2020 to 2023**: 20 cells.
- **C** every signal `run_backtest(counterfactual=True)` simulates for BTC futures:
  primary 2022-01-01 to 2025-10-01 (contaminated), confirmation 2025-10-01 to
  2026-10-09.

Unresolved entries stay in at 0.0 and are counted. A cell that fails to fetch is retried
until it yields a row, never dropped.

## Criteria

Same as H-TPF.

**Arm A and Arm B, each:**
1. the paired mean favours the candidate in **>= 80% of cells** (>= 8/10 for A,
   >= 16/20 for B);
2. the pooled paired mean (entry-weighted, every entry once) is **>= +0.05pp**.

**Arm C:** paired mean **> 0 in both windows**, and **> 0 for BUY and for SELL**
separately (pooled). No size bar, no interval. It can veto, never carry a PASS.

**Verdict:** PASS if A, B and C all hold. FAIL if A's pooled paired mean <= 0 **or**
B's <= 0. INCONCLUSIVE otherwise.

Reported, never criteria: each arm's baseline and candidate absolute mean P&L; win rate;
the TIME_EXIT share per arm (a tighter trail changes hold length); the share of
**peak-to-exit give-back** (best excursion minus final P&L, in ATR) per arm, since give-back
is what motivated the test; BTC-only and ex-BTC figures; `n_eff`; unresolved counts.

## Discard

- **Fidelity:** Arm C's baseline replay must equal `run_backtest`'s own P&L on every
  recorded signal (max |diff| <= 1e-9). Any difference discards the run.
- **Geometry once:** the baseline stop distance must equal `stop_distance_mult` x the
  engine's stop on a probe signal, asserted before the scored run (as in H-TPF).
- **Candidate inert:** if the paired diff is zero on more than 90% of Arm C's entries,
  C is reported as such. It does not discard A or B.

## What each verdict licenses

- **PASS:** a futures `trailing_atr_factor` of 2.5 becomes a **tested change** in
  `run4-backlog.md`. The owner may ship it mid-run, restarting every futures-scored
  hypothesis from the deploy, as on 2026-10-14 and 10-17. Not applied to spot.
- **FAIL or INCONCLUSIVE:** nothing changes. No other trail value may be tried on these
  grids or windows.
- Whatever the verdict, no profit claim: a trail that beats the live one is
  "less bad than shipped", and the absolute mean is reported beside it.

## Instrument

`scripts/trail_futures_ic.py`, a copy of H-TPF's structure
(`scripts/tp1_futures_ic.py`): same arms, fetch retries, recording spy and discard
checks, with the candidate expressed as `exit_params` instead of a level rewrite. It is
committed **before** the scored run, with a unit test of its verdict function. Its smoke
run produces counts only. Raw output goes to `2026-11-10-futures-trail-run/`.
Because Arm C replays two multi-year backtests (about 50 minutes), the scored run is
started once, from the committed instrument.
