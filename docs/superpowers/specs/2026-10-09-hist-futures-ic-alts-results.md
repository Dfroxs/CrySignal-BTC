# Results — H-F2/H-F3: basis and funding in five other perps

Scored against `2026-10-09-hist-futures-ic-alts-prereg.md` (`6ed4d46`), committed before
any non-BTC data was fetched. Per-symbol output is in `2026-10-09-hist-futures-ic-alts-run/<SYMBOL>/`
(`full.txt/json` = H-F2, `recent.txt/json` = H-F3, `sha256.txt`; CSVs gitignored).
**The VERDICT column `hist_futures_ic.py` prints applies H-F's BTC rule, not this
pre-registration's.** The verdicts below apply the rules here by hand from the printed
ICs and intervals. No symbol tripped a power guard or a missing-data discard.

## H-F2 — full window, 95% CI, sign fixed negative

| symbol | basis IC [95% CI] | below 0? | funding IC [95% CI] | below 0? |
|---|---|:-:|---|:-:|
| ETH | −0.025 [−0.047, −0.004] | ✓ | −0.043 [−0.072, −0.017] | ✓ |
| SOL | +0.005 [−0.019, +0.027] | | +0.006 [−0.025, +0.035] | |
| BNB | −0.035 [−0.060, −0.011] | ✓ | −0.023 [−0.053, +0.006] | |
| XRP | −0.045 [−0.066, −0.024] | ✓ | −0.040 [−0.067, −0.013] | ✓ |
| DOGE | −0.013 [−0.037, +0.008] | | −0.014 [−0.046, +0.014] | |
| **count** | | **3 / 5** | | **2 / 5** |

No interval lies entirely above 0. PASS needs ≥ 4 and FAIL needs ≤ 1, so:
**basis INCONCLUSIVE, funding INCONCLUSIVE.**

## H-F3 — 2025-01 → 2026-08, 90% CI

| symbol | basis IC [90% CI] | funding IC [90% CI] |
|---|---|---|
| ETH | +0.001 [−0.033, +0.027] | −0.021 [−0.073, +0.022] |
| SOL | +0.023 [−0.013, +0.058] | +0.037 [−0.009, +0.083] |
| BNB | **−0.060** [−0.107, −0.016] | −0.029 [−0.077, +0.018] |
| XRP | **−0.038** [−0.084, −0.004] | **−0.052** [−0.104, −0.012] |
| DOGE | −0.008 [−0.042, +0.023] | −0.024 [−0.077, +0.023] |
| negative / CI below 0 | 3 / 2 | 4 / 1 |

PASS needs ≥ 4 negative **and** ≥ 2 with the CI below 0. FAIL is ≤ 2 negative.
**basis INCONCLUSIVE (3 negative), funding INCONCLUSIVE (4 negative, but only 1 CI
below 0).**

## What it means

- **The effect is not a BTC fluke, but it is weak and patchy.** Across six markets
  (BTC + these five), the full-window basis IC is negative in 5 of 6, with intervals
  below 0 in 4 (BTC, ETH, BNB, XRP). Funding is negative in 5 of 6, with intervals below
  0 in 3. It is clearest in the majors and absent in SOL.
- **It has not clearly decayed, and it has not clearly survived.** In 2025–2026,
  funding is still negative in 4 of 5 symbols, but most intervals reach zero. BTC's own
  2025–2026 sign flip is not shared by BNB or XRP.
- **Size:** |IC| 0.01–0.06. Even where it is real, it is a tilt worth a fraction of a
  round trip's cost, not a strategy.

## What is licensed

Per the pre-registration, the run-4 z-score condition is licensed only by H-F2 PASS
and H-F3 PASS. **Neither passed, so the condition is not built.**

What the evidence does support, without an edge claim: **the engine should stop
scoring a high basis as BULLISH.** Across six markets, no full-window interval supports
that reading, and four reject it. Neutralising the basis condition (scoring it 0) is a
correctness fix for run 4. Flipping it would need the condition test that has not
passed.
