# Results: does a breakeven lock help futures' exit? (H-PLK)

Scored against `2026-11-11-futures-profit-lock-prereg.md` (`7b35144`, with its same-day
amendment). Knob and instrument at `08327fb`, smoke (counts only) at `50a9ed0`, before the
scored run. Raw output: `2026-11-11-futures-profit-lock-run/run.txt`, `result.json`.

**Run history, disclosed.** The first scored run was stopped by hand during Arm C's
confirmation window, after Arms A, B and Arm C's primary window had printed and before any
summary, `result.json` or verdict existed. Its output is kept as
`run-attempt1-interrupted.txt`. Part of its tail (Arm B cells and the primary window line)
was seen before the verdict. The scored run is the same code on the same data and
reproduces those lines, so nothing was chosen on them. The loader again fell back to
`data-api.binance.vision` (spot OHLCV).

**Discard checks passed:** geometry applied once; Arm C's baseline replay equals
`run_backtest`'s own P&L on all 625 + 178 signals (max |diff| 0.0); unresolved entries 0 in
every arm; the lock armed on 26% and 29% of Arm C entries (limit: 10%). The identity check
of the new knob is in `identity.txt` (0 differences over 18,900 simulated entries).

## Verdict: FAIL

| arm | cells favouring | pooled paired diff | criterion |
|---|---|---|---|
| A, synthetic 2024-25 | **6 / 10** (need 8) | **+0.0034pp** (need >= +0.05) | fails |
| B, synthetic 2020-23 | **7 / 20** (need 16) | **-0.0153pp** | fails |
| C primary, engine, n=625 | | -0.0198pp | must be > 0: fails |
| C confirmation, engine, n=178 | | -0.0113pp | must be > 0: fails |
| C by direction, pooled | BUY n=441 / SELL n=362 | **+0.0001 / -0.0398pp** | SELL negative |

B's pooled paired mean is <= 0, so per the pre-registration the verdict is **FAIL**. Arm C
could only veto and also fails.

## What it says

- **The effect is close to zero, not clearly harmful.** This differs from H-TPF and H-TRL,
  which lost 0.07 to 0.27pp per entry. Here A is +0.003pp and B is -0.015pp: nothing
  measurable in either direction, against a bar of +0.05pp.
- **The lock arms often and rarely decides the trade.** It armed on 39% (A) and 37% (B) of
  synthetic entries, but only 10-11% of entries then exited at the locked floor.
- **It does not reduce give-back.** Best-excursion-minus-final-P&L moved 3.84 to 3.81pp (A)
  and 4.89 to 4.87pp (B). The give-back the run-3 observation pointed at is mostly on
  trades that never reach +1R, or that the trail already catches.
- **Win rate falls** (34.2% to 31.2% in A, 35.3% to 31.9% in B): stopping out at entry turns
  some would-be recoveries into flat-to-small-loss exits, offset by the losses it cuts.
- **SELL is where it hurts** (-0.040pp pooled in C), BUY is flat (+0.0001pp).
- Absolute P&L stays negative: A baseline -0.115pp, with the lock -0.112pp.

## What is licensed

Nothing changes. No live code and no config. The `be_lock_r` knob stays in the simulator,
default off. **No other lock trigger or level may be tried on these grids or windows.**

With H-TPF (TP1 distance) and H-TRL (trail width), all three exit-side candidates the
run-3 observation suggested are now tested and none helps. The exit side of the futures
system is not where the next gain is. What remains is entry timing and the live-only
fields (H-V1, H-V2, H-S, H-X, scored 2026-11-16), each needing its own pre-registration.
