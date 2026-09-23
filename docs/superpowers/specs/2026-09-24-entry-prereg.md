# Pre-registration — does the assembled entry beat a dart?

**Written while `scripts/entry_ic.py` was already running and its log was still empty.**
Instrument committed at `078ec90`, before it had produced a single figure. Verify with
`git show 078ec90` and the run log's first-line timestamp.

## Why this experiment exists

`CLAUDE.md` says development is stopped because STEP 1 tested all 22 scoring conditions
across 8 assets and 7 years and none survived a change of market. But STEP 1 tested the
**conditions**. It never tested:

- the **five veto gates**, which reject an entry the scoring already accepted, and whose
  widths were tuned on one 90-day BTC file (`backtest.py` comment at `engine.py:800`);
- the **assembled system** against the only baseline that matters — random entry.

Over the live paper run's first 24 days the gates rejected 127 of 139 scored futures
cycles and 68 of 90 spot cycles. Nothing with that much authority in this system has
ever been tested out of sample.

The sibling project (`../Nakhoda`) has the missing controls and the missing data. Its
`candidate-v2` cleared every tuning criterion and then **lost to random entry** on the
holdout, −0.13R against +0.37R. Without that baseline it would have been traded.

## Data

Nakhoda's OKX 4h cache: its ten `tuning` symbols, every candle before `2025-08-30`.
This repository has never touched any of it, so all of it is out of sample **here**.
Nakhoda's ten locked holdout symbols are not opened — that budget belongs to that project.

Per symbol, evaluation starts only once BOTH the 1D and 1W EMA200 have 200 bars of
history, because before that `htf_indicator_series` labels every bar BEARISH and the
counter-trend gate would reject everything for a warmup artefact. Live never sees this.

## Known limits, stated before the result

- **Market-structure conditions score NEUTRAL** — funding, L/S, OI, basis, DXY, S&P,
  gold, VIX, stablecoin, BTC.D. No free historical API serves them; `backtest.py` has
  the same hole and `CLAUDE.md` calls results "conservative" for it. The engine here is
  therefore quieter than the live bot. It applies to both engine arms equally.
- **Fixed threshold `SPOT_THRESHOLD` (4.3).** The live adaptive controller moves between
  3.8 and 5.3. A fixed bar is what `backtest.py` uses; moving it to raise the entry count
  would be tuning against the answer.
- **Entries are simulated independently.** No `open_until` blocking, because `CLAUDE.md`
  records that it makes sequence comparisons here unreadable. This measures the quality
  of an entry POINT, not of a portfolio.
- **Donchian is in-sample on these symbols** — Nakhoda tuned it on exactly this set. Its
  figure is reported as descriptive only and can never count as confirmation.

## Hypotheses and criteria

**H-A — the assembled entry beats random entry.**
PASS if `spotsignal.mean − random.mean > 0` **and** spotsignal's 90% bootstrap interval
on the mean excludes `random.mean`. FAIL otherwise.
*Power guard:* if `spotsignal.n < 100`, the verdict is **INCONCLUSIVE**, not a pass or a
fail, and no other reading of H-A may be reported.

**H-B — the three anti-chase gates earn their place.**
PASS if `spotsignal.mean > no_antichase.mean`. FAIL if `no_antichase.mean >=
spotsignal.mean`. A FAIL means the gates that make this a pullback-only system cost more
than they save, and the answer to "can it do more than pullbacks" is *remove them*,
not *add a breakout mode*.
*Power guard:* the same `n < 100` rule applies to whichever arm is smaller.

**H-C — descriptive only.** Donchian's mean against both engine arms. Reported, never
used to justify a change, because these are its own tuning symbols.

## What would make me discard the whole run

- Any arm resolving fewer than 20 entries pooled — too thin to summarise at all.
- The random baseline's mean differing by more than 0.5pp between the first and last ten
  seeds, which would mean the seeds are not sampling the same population.
- Engine entry rate above 5% of evaluated candles, which would mean the threshold was
  not applied and the arm is not the shipped system.

## What a FAIL on H-A would mean

That 22 conditions, an adaptive controller and five gates select entries no better than
chance — and the honest next step is subtraction, not addition. It would also make H-B
moot: you cannot improve the gating of a signal that has no edge to gate.
