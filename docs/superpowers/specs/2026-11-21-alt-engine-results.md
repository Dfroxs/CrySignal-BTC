# H-EA results: the BTC bot's rules do not pick direction on the scanner's altcoins (FAIL)

**Scored 2026-11-21**, once, under `2026-11-21-alt-engine-prereg.md` (`6e8624a`; script
`874ddc7`, checkpointing added `11fd11e` after the first run died on a Binance timeout). The
rerun reproduced the first run's six finished cells exactly.

## H-EA-1: FAIL

| criterion | needed | got |
|---|---|---|
| cells with mean `d = engine − mirror` > 0 | ≥ 12 of 16 | **9 of 16** |
| pooled mean `d`, cluster-bootstrap 90% CI | lower bound > 0 | **+0.197 pp**, CI **[−0.137, +0.537]** |

Per-cell mean `d` (pp):

| | 2024 | 2025 |
|---|---|---|
| ETH | +0.30 | −0.38 |
| SOL | −0.15 | +1.11 |
| BNB | +0.62 | +1.19 |
| XRP | −1.58 | +0.14 |
| DOGE | +0.36 | +0.95 |
| ADA | +1.73 | −0.15 |
| LINK | −0.75 | −0.30 |
| AVAX | +0.84 | −0.09 |

## Reported only

- 1,166 closed trades; no cell under 10.
- The engine's own net P&L is +0.02 % per trade on the mean of cells, positive in 8 of 16.
  Its win rate is 37 %.
- Outcomes: 241 WIN, 703 LOSS, 221 TIME_EXIT, 1 VOL_EXIT.

## What this licenses (per the prereg)

The bot's rules do not pick direction on these altcoins. The sign of the edge changes with
coin and year, which is the STEP 1 pattern again.
- **A live "Engine" book** may still be built purely for data yield, as an owner's design
  decision and never a profit claim. The recommendation is not to: the rule set is already
  known to be break-even after costs here.
- **Re-scoring:** none on other coins, years, timeframes or parameters.
