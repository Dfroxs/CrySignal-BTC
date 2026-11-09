# Pre-registration: is futures' TP1 too far away? (H-TPF)

**Written 2026-11-10, before the instrument exists and before any figure below has been
computed.** Modelled on `2026-10-15-tp1-distance-prereg.md` (H-TP, spot), which it does not
repeat: H-TP tested spot's 4h TP1 and ended INCONCLUSIVE. Futures has its own exit
geometry (`2026-10-09-futures-exit-prereg.md`) and has never had TP1's distance tested.

## Why

One observation, made before this document, from run 3's futures book
(`signal_history.db` snapshot 2026-11-09 19:00 UTC, positions #4–#15, ids 8–15 closed):

- **No futures position has ever reached TP1.** Seven closed: two WIN (+0.19, +0.18), two
  LOSS, one MACRO_CLOSE, two FLIP. Best excursion measured on hourly closes, as a share of
  the TP1 distance: #4 67%, #10 47%, #11 40%, #13 68%, #15 23%. #8 and #14 never moved in
  the position's favour at all, so their TP1 distance is irrelevant to them.
- Live TP1 sits at ~1.5 × the stop distance on every position (e.g. #14: TP1 1.72% vs SL
  0.87%; #16: 5.66% vs 3.77%). After `apply_futures_exit_geometry` doubles stop and
  target distances, TP1 is about +7.5 ATR on a 1h chart.
- Two winners gave their move back before closing: #13 peaked +3.48% and closed +0.18%,
  #4 peaked +1.67% and closed +0.19%.

n = 7. This is a reason to look, not a result. The spot precedent is a warning: a nearer
TP1 won on random entries in 27 of 30 cells and lost slightly on the entries the engine
actually takes (H-TP arm C), probably because winners tend to run. Whether futures
behaves like spot is the question.

**What this does not repeat.** The trail (1.5 → 3.5 ATR) and the stop distance (×2) were
tested and shipped on 2026-10-09. A tighter trail is **not** tested here. It is a second
knob on the same exit, and testing it alongside TP1 would be the sweep this repository
forbids. If H-TPF resolves, the trail may get its own pre-registration after it.

**Contamination, disclosed.**

1. The candidate below (R:R 1.0) lies inside the 40–68% of TP1 distance that four live
   positions reached. Those positions are the observation that suggested it, so they are
   not evidence and are never an arm.
2. Arm C's primary window, 2022-01 → 2025-10, is the window that tuned the current
   geometry (stop × 2, trail 3.5). Arm C is a guard there, not evidence.
3. Arm C's confirmation window ends **2026-10-09**, before run 3 began, so no live
   position overlaps it.

## The one candidate (fixed now)

**TP1 at 1.0 × the position's own stop distance** (R:R 1:1), TP2 at twice TP1's distance.
Everything else stays exactly as live: the stop, the trail (3.5 ATR, 0.8 × after TP1),
the 50% partial, the 72h hold cap, the volatility exit, the funding exit, and costs.

Applied **at open, after every gate and after `apply_futures_exit_geometry`**, so entries
and gates are unchanged. For a position with live levels `entry`, `stop_loss`,
`take_profit`, `tp2`, with `d = |entry − stop_loss|`:

`TP1' = entry ± min(|take_profit − entry|, 1.0 × d)` in the position's direction, and
`TP2' = entry ± min(|tp2 − entry|, 2 × |TP1' − entry|)`.

A target already nearer than the candidate is never moved further out.

Why 1.0: it is the nearest TP1 that still pays the stop, so it is the smallest change that
can matter. One candidate only: **no sweep of R multiples is run or reported.**

## Arms

All arms use `backtest._simulate_forward` in futures mode (1h, max hold 72 bars, live
futures costs, the live futures exit geometry). Each entry runs twice, baseline levels and
candidate levels. The comparison is **paired**: `diff = pnl(candidate) − pnl(baseline)`
per entry.

- **A — synthetic entries, fresh data.** `scripts/exit_ic.py`'s method with `--mode
  futures`: every 24th 1h candle after warmup becomes a hypothetical BUY at
  `exit_ic._signal_at` levels, then the live geometry is applied. Assets BTC, ETH, BNB,
  XRP, LINK (USDT, Binance), years **2024 and 2025**: **10 cells**.
- **B — synthetic entries, earlier years.** Same method, same assets, **2020–2023**:
  **20 cells**.
- **C — engine entries (guard).** Every signal `backtest.run_backtest` simulates for BTC
  futures, taken and gate-blocked alike, recorded as `trail_ic.py` records them.
  Primary window 2022-01-01 → 2025-10-01 (**contaminated**, see above); confirmation
  window 2025-10-01 → **2026-10-09**.

Unresolved entries stay in the sample at 0.0 and are counted, never grounds to drop a
cell. A cell that fails to fetch is retried until it produces a row, never dropped.

Synthetic entries are BUY only. SELL is covered by Arm C alone, and reported.

## Criteria

**Arm A** and **Arm B**, each:
1. **Sign consistency:** the paired mean diff favours the candidate in ≥ 80% of the arm's
   cells (≥ 8 of 10 for A, ≥ 16 of 20 for B).
2. **Effect size:** the pooled paired mean (every entry concatenated once, entry-weighted)
   is **≥ +0.05pp per entry**.

**Arm C:** the paired mean diff is **> 0 in both windows**. No size bar, no interval. As a
partly contaminated guard it can veto but never carry a PASS. It must also hold in SELL
and BUY separately over the pooled windows (**no direction reversal**: both > 0).

**Verdict:**
- **PASS** if A meets both criteria, B meets both, and C holds.
- **FAIL** if A's pooled paired mean ≤ 0, or B's is ≤ 0.
- **INCONCLUSIVE** otherwise.

Reported, never criteria: each arm's TP1-hit rate under both levels, `n_eff`, the
unresolved count, BTC-only figures, and absolute P&L per arm (a TP1 that is nearer but
still loses money is not a profit claim).

## Discard

- **Fidelity:** Arm C's baseline replay must equal `run_backtest`'s own P&L on every signal
  it records, as the 2026-10-09 exit test required. Any difference → DISCARDED. The
  instrument records the count and the max absolute difference.
- **Candidate inert:** if TP1' equals the baseline TP1 on more than 90% of Arm C's
  entries, C is reported as such. That does not discard A or B.
- **Geometry double-apply:** the 2026-10-09 results note that `backtest.py` already
  widens futures signals. The instrument must apply the candidate **after** that and
  must assert baseline stop distance equals `stop_distance_mult × the engine's stop`
  on one recorded signal before the scored run, or the run is discarded.

## What each verdict licenses

- **PASS:** futures TP1 at 1.0 × stop distance becomes a **tested change** in
  `run4-backlog.md`, implemented as an exit geometry applied at open beside
  `apply_futures_exit_geometry`. The owner may ship it mid-run, which restarts every
  futures-scored hypothesis from the deploy, as on 2026-10-14 and 10-17. It is not
  applied to spot.
- **FAIL or INCONCLUSIVE:** nothing changes. No other R multiple may be tried on these
  grids or windows.

## Instrument

`scripts/tp1_futures_ic.py`, reusing `exit_ic` (synthetic entries, `_signal_at`) and
`trail_ic`'s signal recording, committed **before** the scored run. Its smoke run
produces counts only: entries per cell for one A cell, and C's universe size per window.
Raw output goes to `2026-11-10-futures-tp1-run/`.
