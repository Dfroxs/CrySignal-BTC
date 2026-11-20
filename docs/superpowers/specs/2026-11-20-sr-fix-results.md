# H-SR results: the repaired S/R condition does not predict (FAIL)

**Scored 2026-11-20**, once, under `2026-11-20-sr-fix-prereg.md`, with the shipped engine
(`develop` at the prereg commit).

## H-SR-1 (offline): FAIL

`scripts/condition_ic.py --matrix --only support_resistance --mode futures` over BTC, ETH,
SOL, BNB, XRP × 2024, 2025. The statistic is the Welch t of the forward return after a BUY
fire minus after a SELL fire, so positive means the condition points the right way.

| horizon | BTC 24 | BTC 25 | ETH 24 | ETH 25 | SOL 24 | SOL 25 | BNB 24 | BNB 25 | XRP 24 | XRP 25 | positive | needed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | −0.9 | −0.8 | +1.1 | +0.1 | −0.2 | +1.9 | +1.0 | −0.8 | +0.2 | −0.4 | 5 | ≥ 7 |
| **6** | −1.2 | +0.1 | +1.1 | −1.6 | +1.0 | **+2.9** | −0.8 | −1.2 | +0.3 | −0.4 | **5** | **≥ 8** |
| 12 | +0.6 | +0.5 | +0.9 | −1.9 | +0.4 | **+3.5** | −1.0 | −1.7 | −0.0 | +0.9 | 6 | ≥ 7 |
| 24 | +0.1 | +0.0 | −0.6 | −1.4 | +1.0 | +0.3 | −0.5 | **−2.1** | +0.7 | −0.1 | 4–5 | ≥ 7 |

- **Every criterion fails.** The sign is a coin flip across cells: 5 of 10 at the main
  horizon.
- **No cell rescues it.** The one strong cell (SOL 2025) is one market in one year. BNB 2025
  points the other way at 24 bars. Per the prereg, no other cells, horizons or years are
  tried.
- **Not run:** spot (4H) was "reported only" and was not run. Nothing it showed could
  change the verdict.

## What this licenses (per the prereg)

The repaired condition stays live, for data only, until the bot next restarts for another
reason. At that restart it goes into `DISABLED_CONDITIONS`, which lowers both ceilings by
0.75 and the thresholds with them. Its live record (H-SR-2) keeps accumulating in
`cycle_log.contributions` until then and is reported, never judged.

This matches STEP 1: a public technical condition, tested across markets and years, with no
sign that holds.
