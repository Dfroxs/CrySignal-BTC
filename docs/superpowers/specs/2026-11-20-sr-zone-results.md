# H-SR2 results: S/R zones do not predict either (FAIL)

**Scored 2026-11-20 (18:48 UTC)**, once, under `2026-11-20-sr-zone-prereg.md`. The prereg was
committed at `e526099` before any code; the code is at `b3e60f1`. The power guard passed:
BTC 1H fires on 25.3% of bars against a 1% floor.

## H-SR2-1 (offline, futures 1H on 4H zones): FAIL

The statistic is the Welch t of the forward return after a BUY fire minus after a SELL fire.

| horizon | BTC 24 | BTC 25 | ETH 24 | ETH 25 | SOL 24 | SOL 25 | BNB 24 | BNB 25 | XRP 24 | XRP 25 | positive | needed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | **−3.0** | −1.1 | −1.3 | −0.1 | **−2.7** | +0.8 | +0.0 | −1.2 | −1.3 | +0.6 | 2–3 | ≥ 7 |
| **6** | **−3.7** | −0.4 | +0.6 | −0.3 | **−2.2** | +1.7 | +0.2 | −1.0 | −0.8 | **+2.7** | **4** | **≥ 8** |
| 12 | **−2.5** | −0.9 | +1.8 | +1.2 | −0.9 | +0.5 | −0.3 | −1.5 | −0.9 | **+2.5** | 4 | ≥ 7 |
| 24 | **−3.6** | −0.0 | +0.6 | −0.8 | +0.0 | +0.6 | −0.3 | −1.4 | −1.0 | **+2.3** | 3–4 | ≥ 7 |

- **Every criterion fails.** No cell is thin: each has hundreds of fires on both sides.
- **It is not an inverted signal either.**
  - BTC 2024 and SOL 2024 are strongly negative, and XRP 2025 strongly positive.
  - A sign that flips between markets and years is no sign.
  - Per the prereg, a negative that held everywhere would have been a new hypothesis. It
    does not hold.
- **Not run:** spot (4H on 1D zones), reported only, was not run. Nothing it showed could
  change the verdict.

## What this licenses (per the prereg)

- **S/R as a scoring bonus is closed.** Both forms tested fail by the same standard: the
  crude 50-bar rule (H-SR) and the trader's HTF zones (H-SR2). That matches STEP 1, where no
  public indicator held its sign across markets and years.
- **`sr_zone` stays in `DISABLED_CONDITIONS`.** Its code ships at the next restart made for
  another reason, so it is recorded live in `cycle_log.disabled_contributions`.
- **S/R keeps its non-scoring roles,** untouched by both tests: the TP caps at
  resistance/support, `sr_first`, and the pyramid gate.
- **What would be new:** a different role, for example a Phase 3 location filter judged on
  trade outcomes, needs its own prereg. Nothing here is re-scored on other cells, horizons,
  years or parameters.
