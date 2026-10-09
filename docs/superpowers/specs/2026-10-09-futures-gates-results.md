# Results — do the futures entry gates earn their place under the new exit?

Scored against `2026-10-09-futures-gates-prereg.md`, run at `234cf87` (prereg commit;
instrument `46db2e8`), 2026-10-09 07:55 → 08:35 UTC. Raw output:
`2026-10-09-futures-gates-run/run.txt`, `result.json`.

## Verdict: all five gates INCONCLUSIVE, by the power guard. No gate is removed.

| gate | only-blocked n | mean (pp) | verdict |
|---|---:|---:|---|
| fakeout_first | 1 | −1.415 | INCONCLUSIVE |
| regime_counter | 5 | +0.451 | INCONCLUSIVE |
| trend_confluence | 0 | — | INCONCLUSIVE |
| psy_sl_first | 0 | — | INCONCLUSIVE |
| sr_first | 5 | −0.980 | INCONCLUSIVE |

`FUTURES_CONFIG["entry"]["disabled_gates"]` stays empty.

## Why the test could not resolve: a design flaw, stated plainly

The statistic used signals blocked by a gate **alone**. The futures gates overlap almost
completely, and nearly every blocked signal also fails the confidence gate, which was
excluded from the test and so always shares the block. Only 11 of 265 blocked signals
had a single tested gate as their sole reason. The "only" design measures exactly what
removing one gate admits, which is why it was chosen, but on this stack it leaves
nothing to measure. A test that can resolve these gates has to remove them *jointly* or
in a fixed order, and is pre-registered as such before being run.

## Descriptive, as pre-declared: the stack as a whole

| | n | mean per trade |
|---|---:|---:|
| kept (taken) | 25 | **+0.497pp** |
| all blocked (shadows) | 265 | −0.071pp |

- **The 2025-10 → 2026-10 lead did not replicate.** In the year that suggested the gates
  hurt, kept trades came to −0.104pp and blocked ones +0.046pp. On 2022-01 → 2025-10 the
  stack separates the right way: kept is far better than blocked. This is the
  period-to-period sign flip seen throughout this project, and the lead is recorded as a
  one-year artefact.
- **Futures with the new exit and the full gate stack: +0.497pp per trade over 25 trades,
  2022–2025.** That is descriptive, on the same window where the exit's paired test
  passed. It is the first positive futures figure under live-equivalent replay, but 25
  trades over 3.75 years is too few to call an edge.
