# Results — does the spot regime gate earn its place? (H-RG)

Scored against `2026-10-09-regime-gate-prereg.md` (`e18840f`). Instrument
`scripts/regime_ic.py` committed at `1a3d488` before the scored run, with a smoke run
that produced counts only. Raw output: `2026-10-09-regime-gate-run/run.txt`,
`result.json`. Discard checks passed: no `regime_counter` failure outside a bearish
TRENDING/VOLATILE regime.

The engine ran as deployed at `316e0dc` (spot anti-chase vetoes off) on 10 OKX 4h
symbols through the live spot exit.

## Verdicts

| window | regime_only | kept | random (same regime) | kept − regime_only [90% CI] | H-RG1 | H-RG2 |
|---|---|---|---:|---|---|---|
| primary (→ 2025-08-30) | n=82, **−1.367pp** [−2.064, −0.655] | n=474, −0.359pp | −0.379pp | **+1.009 [+0.201, +1.850]** | **PASS** | FAIL |
| confirmation (2025-08-30 → 2026-08-30) | n=4, +0.015pp | n=51, −0.222pp | −2.057pp | — | INCONCLUSIVE (n < 10) | INCONCLUSIVE |

**Overall: INCONCLUSIVE.** The pre-registration requires both windows to pass, and the
confirmation has only 4 entries. **Per the pre-registration, the gate stays.**

## What it says

- **In the window with power, the gate does its job, and by a wide margin.** The BUYs it
  alone blocks lost 1.37pp per trade, against 0.36pp for the trades the system kept: a
  1.0pp difference whose interval clears zero. They were also worse than *random* entries
  in the same regime (−0.38pp, H-RG2 FAIL for the engine). Inside a bearish ADX trend,
  the engine's BUYs are worse than chance. That is exactly what this gate removes.
- BTC points the same way (−0.77pp blocked against −0.29pp kept, n=23), and so do 7 of
  the 8 symbols that had any regime-only entries.
- **It also matters live.** At 13:01 today it blocked the first spot BUY to clear the bar
  after the anti-chase change. This result says that block was very likely a good one.
- About half of the BUYs this gate blocks also fail `trend_confluence` (gate combos in
  `result.json`), so its unique contribution is the `regime_only` arm measured here.

## What is licensed

Nothing changes. `regime_bearish` stays for spot, in run 3 and run 4. It is the first
Phase 3 gate in this repository with a measured selection effect, so it should not be
traded away for data yield.
