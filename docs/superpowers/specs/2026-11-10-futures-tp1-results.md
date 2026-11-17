# Results: is futures' TP1 too far away? (H-TPF)

Scored against `2026-11-10-futures-tp1-prereg.md` (`65b496b`). Instrument
`scripts/tp1_futures_ic.py` committed at `7b62ece`, smoke run (counts only) at the next
commit, before the scored run. Raw output: `2026-11-10-futures-tp1-run/run.txt`,
`result.json`.

**Discard checks passed:** geometry applied once; Arm C's baseline replay equals
`run_backtest`'s own P&L on all 625 + 178 signals (max |diff| 0.0); unresolved entries 0 in
every arm; the candidate acts on every Arm C entry (inert 0%). Note: Binance's primary API
failed during the run and the loader fell back to `data-api.binance.vision` (spot OHLCV).

## Verdict: FAIL

| arm | cells favouring | pooled paired diff | TP1 hit (base -> cand) | criterion |
|---|---|---|---|---|
| A, synthetic 2024-25 | **2 / 10** (need 8) | **-0.0724pp** (need >= +0.05) | 16% -> 45% | **fails** |
| B, synthetic 2020-23 | **9 / 20** (need 16) | **-0.1125pp** | 16% -> 46% | **fails** |
| C primary, engine, 2022-01 -> 2025-10 | n = 625 | +0.0116pp | 16% -> 34% | > 0 holds (contaminated) |
| C confirmation, engine, -> 2026-10-09 | n = 178 | +0.0477pp | 16% -> 41% | > 0 holds |
| C by direction, pooled | BUY n=441 / SELL n=362 | +0.0467 / **-0.0134pp** | | SELL reverses |

Per the pre-registration: A's and B's pooled paired means are both <= 0, so **FAIL**.
Arm C could only veto, never carry a PASS.

## What it says

- **A nearer TP1 is reached 2.5-3x as often and does not pay.** TP1 hit rises from 16% to
  about 45%, and P&L per entry falls by 0.07-0.11pp. BTC alone: -0.04 / -0.08pp.
- **It is the reverse of spot's H-TP** (27 of 30 cells favoured a nearer TP1 on random
  entries). Futures' geometry (stop x2, trail 3.5 ATR) leaves winners room to run, and
  cutting half off at 1.0R shortens them. The loss is concentrated in strongly trending
  years (2020, 2021, XRP 2024); the candidate is slightly better in 2022.
- **On engine entries it is slightly positive** (+0.01 / +0.05pp), driven by BUY; SELL is
  -0.01pp. That is inside noise and does not change the verdict, but it is the same sign
  disagreement H-TP found between synthetic and engine entries, in the opposite direction.
- Absolute P&L: A baseline -0.115pp, B baseline -0.004pp per entry. Nothing here is
  profitable, and the candidate is worse in both.

## What is licensed

Nothing changes. Futures TP1 stays where it is. **No other R multiple may be tried on
these grids or windows.** The run-3 observation that no position reached TP1 (0 of 7)
is real, but this test shows that moving TP1 closer is not the remedy. The losses that
motivated it look like entry timing (#8, #14 never moved in favour) and give-back through
the 3.5 ATR trail, neither of which this test touched. The trail is a separate
pre-registration if the owner wants it.
