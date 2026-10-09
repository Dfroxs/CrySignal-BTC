# Pre-registration — is the futures exit too tight?

**Written before any test-window figure exists.** Instrument `scripts/trail_ic.py`
committed at `fd96ceb`. The only run so far is a fidelity smoke test on 2025-10-09 →
2025-11-15, outside the test window: 14 signals recorded, base replay identical to the
backtest. It printed no P&L comparison.

## Where the candidate came from, and why that data is excluded

A diagnosis on **2025-10-09 → 2026-10-08** (116 futures signals, taken and blocked):

- Shorts were **not late**. They entered after a +1.13% 24h rise, at 0.65 of the 24h
  range, with RSI 54. Only 1 of 91 followed a > 2% drop. Their 72h direction was
  +0.46pp against random's +0.20pp.
- They lost to the exit. The shipped `trailing_atr_factor` of 1.5 trails from entry at
  roughly 1.1% of price on 1h bars. BTC's median 72h adverse excursion after these
  entries was 2.40%, with a median favourable one of 2.70%.
- Widening only the initial stop changed nothing (losses stayed at ~71 of 91), because
  the trail is what closes the trade. `2026-09-21-exit-*` tested trailing only after TP1,
  never from entry.
- A descriptive grid of six exit settings on that same year improved monotonically as
  the trail widened. **That grid is the data the candidate came from**, so it is excluded
  here. It is not evidence.

## The candidate, one setting

**Stop and target distances × 2.0, `trailing_atr_factor` = 3.5 (futures only).**

Reasoning, stated before running: the median adverse excursion was ~3.2×ATR, so a trail
that survives a normal adverse move must sit outside it. 3.5×ATR does. Doubling the
stop keeps the initial stop outside the same move, and scaling the targets by the same
factor keeps the R geometry the engine built. The setting is not swept.

## Data

**BTC/USDT futures 1h, 2022-01-01 → 2025-10-08**, fetched from the exchange the way
`backtest.py` fetches. That excludes the diagnosis year entirely. Earlier experiments
(`2026-09-21-exit-*`) read futures 1h candles in this span, but none tested a trail from
entry. This is an **a-priori test on previously read data**, not a pristine holdout.

## Statistic and criteria

Every signal `backtest.run_backtest(..., counterfactual=True)` simulates is recorded and
replayed in both arms. **d = candidate P&L − base P&L** per signal resolved in both.

**PASS** requires all of:

1. `n ≥ 100` paired signals (otherwise INCONCLUSIVE, whatever else holds);
2. mean d > 0 **and** its 90% bootstrap interval (iid, 5,000 resamples) entirely above 0;
3. mean d > 0 in **≥ 3 of 4** sequential, equal-count windows;
4. no direction reversal: for SELL and BUY separately, each with n ≥ 20, mean d > 0.

**FAIL** if mean d ≤ 0. **INCONCLUSIVE** otherwise.

## Reported, never criteria

The absolute mean P&L of each arm. A PASS says the wider exit is *better than the
shipped one*. It does not say futures makes money. If the candidate's absolute mean
stays negative after costs, that is reported as plainly as the verdict.

## What a PASS licenses

For run 3: `FUTURES_CONFIG["trailing_atr_factor"] = 3.5`, plus a futures-only stop and
target distance × 2.0 applied in `signals/futures.py` after the engine. Spot is untouched,
since nothing here measured it. A FAIL or INCONCLUSIVE leaves both as shipped.

## What would discard the run

- The base replay differing from `run_backtest`'s own P&L on any signal.
- Fewer than 20 paired signals.
- Any protocol difference from the above.
