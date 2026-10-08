# Pre-registration — does the re-entry anchor need an age limit?

**Written before any result exists.** Instrument `scripts/reentry_ic.py` committed at
`a560e61`, not yet run. The only output seen so far is a smoke test on BTC's first 300
evaluable candles: it printed timing (~17 ms/candle), 6 engine BUYs, 0 taken, 0 shadow
trades. No P&L figure has been produced.

## Why

`2026-10-09-no-positions-diagnosis.md`: the re-entry guard compares a new entry with the
last WIN/LOSS in the same direction, **with no age limit**. In paper run 2, one spot WIN
at $77,361 from 2026-09-12 blocked all 11 spot signals from 09-19 to 10-07 while BTC sat
at ~$84k. Four of the 11 were NORMAL tier. One fell short of the bar by 0.05.

The guard was written to stop re-entering a setup that has just exited at a worse price.
Three weeks later the anchor is not "the setup that just exited". Whether blocking against
it still selects anything is an empirical question, and this test answers it.

## The candidate, fixed here

**`reentry_max_age_hours = 168`** (7 days). An anchor closed longer ago than that is
ignored. One value, chosen before running, and not swept.

The reasoning: spot's hold cap is 72h (18 × 4h), so a week is more than two full holding
periods. Anything inside that window really is the recent setup. Anything outside it is
not. No figure informed the choice.

## Data, and what has already touched it

Nakhoda's OKX 4h cache, its ten `tuning` symbols (BTC ETH SOL BNB OKB ICP SUSHI NEAR UNI
DOGE). The full span ends 2026-08-30. Strict 1D+1W EMA200 warmup, so a symbol contributes
only once both are real.

**These candles are not untouched.** `2026-09-24-entry-*` read them before 2025-08-30,
and `2026-09-24-gates-holdout-*` read the year after. Neither experiment involved the
re-entry guard, its anchor or any age limit, and no parameter tested here was fitted on
them. That makes this an **a-priori test on previously-read data**, not a holdout, and it
is reported as one. The only truly untouched data is what the paper run produces after
2026-08-30, which is far too thin to decide on and will be read descriptively in run 3.

## Protocol

`./venv/bin/python scripts/reentry_ic.py --max-age 168 --out docs/superpowers/specs/2026-10-09-reentry-age-run/result.json`

- **Engine:** `generate_signals` in spot mode, fixed `SPOT_THRESHOLD`, closed bars, real
  HTF resampled from 4h, S/R from the window, market structure NEUTRAL. This is what
  `backtest.py` does.
- **Sequence:** one position at a time, plus the 2-candle same-side cooldown. Every Phase 3
  gate runs through `backtest._failing_gates`. Exits use `backtest._simulate_forward` with
  live's cost model. All of this is imported, not copied.
- **Arms:** `unlimited` (None, today's behaviour) and `aged` (168).

## The statistic

In the unlimited arm:

- **kept**: every resolved trade it took.
- **stale_rejected**: every signal it blocked **for re-entry alone** (no other gate
  failing) **on an anchor older than 168h**, simulated forward as a shadow trade, one
  shadow at a time.

These are exactly the entries an age limit would let through. **`kept − stale_rejected`**:
positive means the stale part of the guard throws away worse entries than the system
keeps, so it earns its place.

## Criteria

**H-R: the stale anchor earns its place.**

- **PASS** if `kept.mean > stale_rejected.mean` **and** the 90% bootstrap interval on
  the difference lies entirely above zero. → `reentry_max_age_hours` stays `None`.
- **FAIL** if `kept.mean ≤ stale_rejected.mean`. → licenses `reentry_max_age_hours = 168`
  for run 3.
- **INCONCLUSIVE** if the direction favours the guard but the interval contains zero.
  → stays `None`.
- **Power guard:** if `kept.n < 100` or `stale_rejected.n < 30`, the verdict is
  **INCONCLUSIVE** whatever the point estimate, and no other reading may be reported.
  The value then stays `None` *as a result*. The owner may still set it on the structural
  argument alone, but that is a design decision and must be recorded as one, not as
  something this test showed.

The burden sits on the guard, as it did in `2026-09-24-gates-holdout-prereg.md`. The
guard was added to erase one losing trade and has never been tested. A FAIL on direction
alone is enough to remove its stale half. It is not enough to remove the guard: anchors
inside 168h keep blocking in both arms.

**Descriptive only, never a criterion:**

- the `aged` arm's n, mean and total against `unlimited`'s;
- breadth: the share of symbols with ≥ 5 stale rejections where kept > stale_rejected.

A sequence comparison between arms is not readable as selection (CLAUDE.md: a different
admit changes every later trade), which is why it is excluded from the verdict.

## What a FAIL licenses, and what it does not

It licenses one config line for run 3: `"reentry_max_age_hours": 168`. It does not say
the system makes money. `2026-09-24-entry-results.md` found the assembled entry no better
than random on tuning data. Unblocking a guard on such an entry makes it trade more. It
does not make it trade well.

## What would make me discard the run

- `kept.n < 20` pooled: too thin to summarise at all.
- `simulate_sequence` or `_failing_gates` raising on real data.
- Any protocol difference from the above other than what this document states.
