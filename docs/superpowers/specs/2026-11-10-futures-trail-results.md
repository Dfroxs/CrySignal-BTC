# Results: is futures' trail too wide? (H-TRL)

Scored against `2026-11-10-futures-trail-prereg.md` (`46bb57c`). Instrument
`scripts/trail_futures_ic.py` committed at `69025ee`, smoke run (counts only) at `bd98863`,
before the scored run. Raw output: `2026-11-10-futures-trail-run/run.txt`, `result.json`.

**Run history, disclosed.** Two earlier attempts died on network errors and wrote no
figure: attempt 1 on a `RequestTimeout` inside `run_backtest`'s own OHLCV fetch (Arm C),
after Arms A and B had printed their per-cell lines; attempt 2 on five failed fetches
before any cell. Their output is kept as `run-attempt1-network-failure.txt` and
`run-attempt2-network-failure.txt`. The retry added at `fcff53d` re-asks a failed fetch
and changes nothing that is computed. The per-cell lines of attempt 1 were seen before the
verdict existed; the scored run reproduces the same data, so no choice was made on them.
The loader fell back to `data-api.binance.vision` (spot OHLCV) throughout.

**Discard checks passed:** geometry applied once; Arm C's baseline replay equals
`run_backtest`'s own P&L on all 625 + 178 signals (max |diff| 0.0); unresolved entries 0 in
every arm; the paired diff is zero on 11% of Arm C entries, so the candidate acts on
89% of them (inert limit 90%).

## Verdict: FAIL

| arm | cells favouring | pooled paired diff | criterion |
|---|---|---|---|
| A, synthetic 2024-25 | **2 / 10** (need 8) | **-0.1482pp** (need >= +0.05) | fails |
| B, synthetic 2020-23 | **3 / 20** (need 16) | **-0.2706pp** | fails |
| C primary, engine, n=625 | | -0.2276pp | must be > 0: fails |
| C confirmation, engine, n=178 | | -0.0984pp | must be > 0: fails |
| C by direction, pooled | BUY n=441 / SELL n=362 | **-0.1692 / -0.2353pp** | both negative |

Per the pre-registration: A's and B's pooled paired means are both <= 0, so **FAIL**. Unlike
H-TPF, Arm C agrees with the synthetic arms in both windows and both directions.

## What it says

- **A tighter trail does what it was meant to and still loses.** Give-back (best excursion
  minus final P&L) falls from 3.84 to 3.29pp in A and 4.89 to 4.22pp in B, and TIME_EXIT
  falls from 14-17% to 3%. Win rate is unchanged (34.2% -> 33.7%, 35.3% -> 33.9%). It
  gives back less per trade and earns less per trade: the trail cuts positions that would
  have recovered and run.
- **Consistent everywhere.** BTC alone -0.12 / -0.30pp, ex-BTC -0.16 / -0.26pp; 5 of 30
  synthetic cells favour the candidate. This extends 2026-10-09's curve (trail 1.5 worse
  than 3.5) to the midpoint: the shipped 3.5 is better than 2.5 as well.
- **The run-3 observation stands but is not the remedy.** #13 and #4 did give back large
  moves (+3.48% to +0.18%, +1.67% to +0.19%). Over thousands of entries a tighter trail
  costs more than the give-back it removes.
- Absolute P&L stays negative: A baseline -0.115pp, candidate -0.264pp per entry.

## What is licensed

Nothing changes. Futures `trailing_atr_factor` stays at 3.5. **No other trail value may be
tried on these grids or windows.** Together with H-TPF, both exit knobs the run-3
observation pointed at (TP1 distance, trail width) are now tested and neither helps. The
remaining candidates need a different structure (a profit-lock trail, deferred in the
prereg, or entry timing), each with its own pre-registration.
