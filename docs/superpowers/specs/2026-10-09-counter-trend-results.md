# Results — does the counter-trend veto earn its place? (H-CT)

Scored against `2026-10-09-counter-trend-prereg.md` (`c70edf7`). The instrument
`scripts/veto_ic.py` was committed at `dc27a21` before the scored run, and smoke-tested
only on 2020-09 (outside both windows). Raw output: `2026-10-09-counter-trend-run/run.txt`,
`result.json`. Both discard assertions held: the configured engine never fired against
the 1D trend, and every vetoed HOLD carried the counter-trend line.

Data: BTCUSDT perp 1h. 49,535 candles were scored, 2021-01-01 → 2026-08-26. All entries
go through the live futures exit (stop/target ×2, trail 3.5×ATR, 72h, costs).

## Verdicts

**Primary question: SELL while 1D is BULLISH**

| window | ct_vetoed (n, mean, 90% CI) | random_ct mean | H-CT1 | H-CT2 |
|---|---|---:|---|---|
| primary 2021–2024 | 73, **−0.535pp** [−1.234, +0.237] | −0.132pp | INCONCLUSIVE (n < 100) | INCONCLUSIVE (n < 100) |
| confirmation 2025–2026-08 | 24, **−0.199pp** [−0.964, +0.611] | −0.122pp | INCONCLUSIVE (n < 30) | INCONCLUSIVE (n < 30) |

**H-CT for shorts against the trend: INCONCLUSIVE.** Both windows fall under the
pre-registered power guard. De-duplication left far fewer independent entries than the
guard assumed.

**Secondary: BUY while 1D is BEARISH**

| window | ct_vetoed | random_ct | H-CT1 | H-CT2 |
|---|---|---:|---|---|
| primary | 54, −1.116pp [−2.204, −0.038] | −0.496pp | INCONCLUSIVE (n < 100) | INCONCLUSIVE (n < 100) |
| confirmation | 32, **+1.144pp** [+0.162, +2.148] | −0.360pp | PASS | PASS |

It passes the confirmation window only. The pre-registration requires both, so **not
passed**. The sign flips between windows, which is the same pattern STEP 1 found
everywhere.

## What it says, within what the guard allows

- **There is no sign that the veto throws away good shorts.** In both windows the
  counter-trend SELLs the engine picked did **worse** than random counter-trend shorts
  (−0.54 vs −0.13 and −0.20 vs −0.12) and lost money after costs. 2021–2022 were
  positive, and every year since was negative. Under-powered, but every point estimate
  leans the same way: these are not shorts worth having.
- **Lifting the veto alone would barely open shorts anyway.** 57 of 73 and 17 of 24 of
  these entries would also fail `regime_counter` or `trend_confluence` in Phase 3.
- **With-trend shorts (`kept`) were not profitable either:** −0.235pp in both windows.
  Futures SELL has no demonstrated edge in either regime.

## What is licensed

Per the pre-registration, nothing passed, so **the veto stays**. Shorts against a
bullish daily trend remain blocked.

Opening them as a data-yield design decision (as was done for the 1.0–1.2× dead zone)
is possible, but **not recommended**. The dead zone had evidence that it was no worse
than NORMAL. Here every point estimate says the extra trades would be worse than random
and would lose money.

## Note for the next test of this kind

The power guards (100 and 30) were set without estimating the de-duplicated count. On
1h with one entry per open position, six years produced 73 + 24 counter-trend shorts.
A future test of a rare entry family should estimate n from the smoke run before fixing
the guard. Changing it now, after seeing the figures, is not allowed.
