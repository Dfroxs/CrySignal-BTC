# PAPER_RUN.md item #5 — does the backtest reproduce the live bot?

Run 2026-09-24 against the paper run's own database (752 cycles, 2026-08-30 → 2026-09-24).
Instrument: `scripts/live_vs_backtest.py`.

## The answer is no, and the reason is structural

`signals/ohlcv.fetch_ohlcv_df` returns the exchange's **current, unclosed candle** as the
last row, and `signals/engine.py:42` scores `df.iloc[-1]`. The live bot evaluates spot at
`:01` past the hour, so the bar it scores is **one minute old** — open, high, low and
close all within a few dollars of each other.

Measured on the live run: the logged `price` sits 8–82 USD from the forming candle's open
on every one of the last eight cycles, and matches no closed candle's close.

`backtest.py` scores closed bars. **The two paths never look at the same bar**, and live's
input cannot be reconstructed afterwards — the exchange serves that candle's final OHLC,
never its state at minute one. So item #5's comparison, as written, can never be exact.
That is a property of the design, not a bug to fix, but it means **no backtest figure this
repository has ever produced describes the system that is running.**

## What the score comparison could and could not show

154 spot cycles, live `buy_score` against the same engine on bars closed at the same
moment:

| | |
|---|---|
| mean difference | −0.090 |
| median | −0.125 |
| range | −2.50 … +2.75 |
| within the replay's blind spot | **154/154 (100%)** |
| verdict disagrees (BUY vs HOLD) | 24/154 (16%) |

**The blind spot is 3.50 of SPOT_MAX_SCORE 22.50.** Spot skips the futures-only conditions
(funding, L/S, OI, basis are `0.00` in `CONDITION_MAX`), but `market_structure` still
scores DXY, S&P500, stablecoin supply and BTC dominance at 0.75 each — 3.00 — and
`gold_vix` adds 0.50. No free historical API serves any of them and `backtest.py` passes
`market_structure=None`.

So the detection floor is 3.50 points and the largest observed divergence is 2.75. **This
test detects nothing, and cannot.** The 16% verdict disagreement is consistent with the
same blind spot moving scores across the threshold, and is not evidence of drift either.

Reported as a null result with its floor stated, rather than as "84% agreement, looks
fine" — which is what the same numbers would say if the floor went unmentioned.

## A figure corrected twice

`2026-09-24-entry-prereg.md` first said the engine was quieter than live because of the
7.5-of-26.5 market-structure hole. That is the **futures** ceiling; I corrected it to
"0.50 of 22.50, essentially the whole system". **That correction was wrong.** Skipping
funding/L-S/OI/basis is not the same as having no blind spot: the real spot figure is 3.50
of 22.50, **15.6%**.

The original claim was right in direction and wrong in magnitude; the correction was wrong
outright. Both are recorded in that document rather than edited away. The entry
experiment's H-A therefore measured an engine running at 84% of its live ceiling, and its
limits section now says so.

## What to do about it

Nothing, to the running bot — the one rule holds. For the **next** run:

1. **Decide whether scoring an unclosed bar is intended.** A bot that must act in real time
   has a case for it, but the indicators on that row are computed over a stub, and the
   entry-wick gate divides by a range of a few dollars. The alternative — score closed bars,
   use live price only for entry and exit levels — would also make backtest and live
   comparable for the first time.
2. **Record `_contributions` in `cycle_log`.** The engine already emits per-condition
   deltas. Storing them would make this comparison sharp instead of blunt: technical
   conditions could be compared directly and the market-structure blind spot subtracted
   rather than tolerated.

---

# Measured, 2026-09-25 — what the unclosed bar actually does

`scripts/forming_bar_impact.py`, 1,200 candles across BTC/ETH/SOL. Both arms go through
the identical indicator function over the identical 1,500-bar window; they differ only in
whether the last row is the closed bar or a stub at that bar's open.

| | |
|---|---|
| verdict differs | **163/1,200 = 13.6%** |
| `buy_score` difference | mean −0.212, **median +0.000**, max \|diff\| **3.75** |

The median is exactly zero: on most candles the forming bar changes nothing. But it
changes the verdict on **one candle in seven**, and can move the score by 3.75 of
SPOT_MAX_SCORE 22.50 — 17%.

The mean is negative, so scoring the unclosed bar makes the system **quieter**, which is
consistent with how little the live run fires.

*A confound found and removed first:* the initial version compared the stub frame
(recomputed) against `load_symbol`'s columns (computed over the symbol's whole history).
That difference is the window, not the bar, and it would have read as "the forming bar
changes everything".

## What this does and does not settle

It settles that the difference is **material, not cosmetic**. It does not settle which
version is *better* — nothing here measures P&L, and no backtest can, since the backtest
is one of the two arms being compared.

The case for closed bars is not statistical:

- The indicators on that row are computed over a bar that has barely begun. `RSI_14`'s
  most recent element has zero range.
- The entry-wick gate divides by that range. At minute one it is measuring the first
  minute's noise, not a rejection.
- Scoring the bar that closed at T uses information that is complete at T. The forming
  bar adds one minute on a four-hour timeframe.
- It would make `backtest.py` and the live path comparable **for the first time**.

The case against changing it is the run's own rule: this moves 13.6% of verdicts, so it
is a strategy change, and it belongs to the next run with the change recorded in that
run's manifest — not to this one.
