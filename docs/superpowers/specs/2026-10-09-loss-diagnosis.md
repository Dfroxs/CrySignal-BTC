# Diagnosis — why do the re-entry test's trades lose?

Descriptive, written after the figures. Nothing here was pre-registered, and every
"candidate" below needs its own pre-registration before it may be called a result.
Source: `2026-10-09-reentry-age-run/trades.jsonl`, the `aged` (168h) arm, 154 resolved
spot 4h trades on nine symbols. The re-run reproduced the scored run exactly, so the
instrument is deterministic.

## Where the loss comes from

| outcome | n | mean (pp) | sum (pp) |
|---|---:|---:|---:|
| LOSS (stop) | 86 | −3.35 | −287.8 |
| TIME_EXIT (72h cap) | 46 | +1.46 | +67.2 |
| WIN | 22 | +4.79 | +105.3 |

- **56% of trades hit the stop.** Only **16% reach TP1**. On average TP1 sits 7.7% away
  and the stop 4.4%, inside a 72h cap.
- **Costs are 0.30pp of the −0.75pp mean**, about 40% of the loss. Gross is −0.45pp.
- Trades that reach the time cap are positive (+1.46pp). The cap cuts survivors that
  are working.

**Exits are not the lever.** `2026-09-21-exit-results.md` already tested and FAILED a
longer spot hold cap (H1), a looser post-TP1 trail (H2) and dropping the TP1 partial
(H3). Re-proposing any of them here would be retuning against the same noise.

## The loss is in the altcoins; BTC is positive

| symbol | n | mean (pp) | sum (pp) |
|---|---:|---:|---:|
| OKB | 44 | −0.82 | −36.2 |
| NEAR | 5 | −6.32 | −31.6 |
| SOL | 17 | −1.33 | −22.6 |
| ETH | 29 | −0.55 | −16.0 |
| DOGE | 17 | −0.87 | −14.8 |
| UNI | 7 | −1.13 | −7.9 |
| **BTC** | **35** | **+0.40** | **+13.8** |

The live bot trades **BTC only**. On BTC, against a random-entry book run through the
same exits:

| source | engine (pp) | random (pp) | engine − random |
|---|---:|---:|---:|
| `2026-09-24-entry` tuning, 2021–2025, independent entries, n=460 | −0.11 | −0.49 | +0.38 |
| `2026-09-24-entry` time holdout, 2025–2026, n=31 | −0.34 | −0.72 | +0.38 |
| this run, aged 168h, sequential, n=35 | **+0.40** | −0.62 | +1.01 |

A random 35-trade BTC book spans [−1.31, +0.11] (5th to 95th percentile). +0.40 sits
above it. In the unlimited arm, BTC took **4** trades. Aging the anchor is what lets
BTC trade at all.

## Why this is a lead, not a finding

- **BTC was picked after seeing nine symbols.** The best of nine looks good by
  construction.
- **BTC is where this system was designed.** Its gate widths came from a 90-day BTC file,
  so BTC is not out of sample for the design, whatever the date windows say.
- The pooled entry test across symbols **lost** to random. The BTC edge, if real, does
  not generalise, and that is the same "survives in one cell only" pattern STEP 1 kept
  finding.
- But it is **consistent across both entry-test windows, at the same size** (+0.38pp),
  and that consistency is what makes it worth a confirmatory test.

## Candidates for pre-registration

1. **BTC beats random under the shipped system.** Confirm on data none of this has seen:
   run 3's live BTC trades, and the variant books (`scripts/variant_books.py`) over the
   same span, against a matched random book.
2. **Costs.** 0.30pp per round trip is 40% of the loss. Limit-order entries (maker fee)
   are a mechanical, non-statistical lever and can be priced in `backtest.py --costs`
   before anything is assumed.
3. **Do not trade the altcoin set with this engine.** Already true live; recorded here so
   no one widens the universe on the strength of the pooled numbers.
