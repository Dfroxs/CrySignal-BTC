# Pre-registration: can Claude pick coins? (H-SC, multi-coin scanner, forward only)

**Written 2026-11-17, before the scanner, its scorer or any opinion exists.** Owner's
request: "signal selain BTC, beberapa koin, dan Claude API untuk scanner". Paper and
log-only. No trade, no position and no BTC-bot decision depends on it.

## Why this, and not the engine on more coins

- The engine on other coins loses. H-FLP Arm D (`2026-11-16-futures-flip-results.md`)
  replayed it on four alts, 2022-01 to 2026-10, 1h futures, live exits: ETH +20.7pp,
  BNB -75.9pp, LINK -148.7pp, XRP -218.7pp. The backlog already says "trading the altcoin
  set with this engine: alts lose, not to re-propose". So the engine is not extended.
- Claude does a different job: compare many coins at once and say which, if any, to trade
  and in which direction. That has never been tested here.
- It cannot be backtested. Any historical candle sits inside the model's training data,
  so a replay would measure memory, not skill. Only opinions given **after** deployment,
  scored against prices that did not exist when it answered, are a fair test.

## The prior, stated

H-S (Claude on the engine's own signals) passed by its registered rule, and then mostly
vanished under episode clustering: within an episode, the hours Claude agreed with did no
better than the ones it disagreed with. LLMs given numbers tend to follow trend and
momentum, which a falling or rising month rewards in one direction. I expect
INCONCLUSIVE or FAIL. It is registered because it is cheap, harmless, and its answer
(either way) is information this project has never had.

## The instrument (fixed now)

**Universe, fixed:** Binance USD-M perpetuals BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK,
AVAX (all `USDT`). No coin is added or removed during the test. A coin that cannot be
fetched in a cycle is passed as missing for that cycle, never silently dropped.

**Cadence:** hourly, a few minutes after the bot's own cycle (cron, `:05`), on closed 1h
bars only. A separate process with its own database (`data/scanner.db`); it does not
import, restart or touch `run_bot.py`, so no running hypothesis' window restarts.

**Input, numbers only** (no news text, no headlines, no free text from any feed: an
injection path, see `run3-plan-agents`). Per coin: last close; returns over 1h, 4h, 24h,
7d; ATR(14) as % of price; volume vs its 24h mean; close vs EMA50 and EMA200 (1h), and
the 24h high/low position; funding rate; basis (mark vs index); open interest and its
24h change; global long/short account ratio; taker buy/sell ratio. Plus the
cross-section: each coin's 24h return minus the universe mean.

**Output:** for each coin `LONG`, `SHORT` or `NONE`, a confidence 0-100, and a reason of
at most 25 words in Indonesian, as one JSON object. Any number of picks, including none.
A reply that does not parse is an error row, never re-asked.

**Model:** Claude only, `claude-opus-5-5` (the model of H-S/H-X), effort low, through
`agents/llm.ask`. Overridable by `SCANNER_MODEL` in the host's `.env`; a model change is
recorded per row and splits the test at that point (each part scored on its own).

**Cost, estimated before building:** about 3.5k input and 1.5k output tokens per call at
the price table in `agents/llm.py` (Opus 5.5 $4 / $20 per MTok) is about $0.044 a call,
**about $32 a month** hourly. Logged per row so the real figure can be checked.

## Criteria (H-SC)

**Pick value.** For a pick on coin `c` at decision time `t` (the close of the scored bar),
direction `d` = +1 LONG, -1 SHORT:

- `r` = coin's close-to-close return from `t` to `t + 24h`, in percent.
- `m` = the mean `r` across the universe coins available at `t`.
- **`excess = d x (r - m)`**: did Claude pick the better coins for that direction, relative
  to picking a coin at random with the same direction. This is the count-matched random
  baseline in closed form (its expected value is exactly `d x (m - m) = 0`), and it
  cancels the month's direction, which is what inflated H-S.

**Unit = UTC day** (hourly picks with a 24h horizon overlap, so hours are not independent;
H-S taught that). Day value = mean `excess` of that day's picks. Days with no pick do not
enter. Interval: **moving-block bootstrap over days, block 7 days, 5,000 draws, seed 2026,
90% two-sided**.

**Verdict:**
- **PASS:** mean day value > 0 and the 90% CI lower bound > 0, with at least **90 scored
  days** and **300 picks**.
- **FAIL:** mean day value <= 0 at or after those counts.
- **INCONCLUSIVE:** otherwise, and **always** below 90 days or 300 picks.
- Scored once at the first scoring date on which both counts are met (checked monthly,
  counts only, until then); never re-scored on a longer window to rescue a result.

**Reported, never criteria:**
- the absolute net return `d x r - 0.20pp` (futures round trip: 0.05% taker plus 0.05%
  slippage each way; `EXECUTION_CONFIG` has 0.04% taker, the backlog's likely real tier is
  0.05%, so the higher figure is used) per pick and per day, which is what a real book
  would have earned before sizing;
- LONG and SHORT separately; by confidence tercile; BTC picks vs the rest;
- the share of `NONE`; picks per day; error and timeout rate; tokens and dollars per month;
- the same statistic at 4h and 72h horizons.

## Discard

- **Health:** if more than 20% of cycles in the scored window are errors or timeouts, the
  run is reported as unhealthy and the verdict is INCONCLUSIVE.
- **Data:** a pick whose coin has no close at `t + 24h` is excluded and counted.
- **Leakage check, once before scoring:** every decision time is strictly after the last
  bar in the prompt, and every `r` starts at that bar's close. A violation discards.

## What each verdict licenses

- **PASS:** pre-registering a **paper** trading book driven by the scanner (fixed sizing,
  stops, costs), scored against the same baseline on fresh days. Not money, not a gate on
  the BTC bot.
- **FAIL or INCONCLUSIVE:** nothing changes. A new prompt, model or universe would be a new
  test with its own prereg, scored only on days after it is written.
- No profit claim either way: H-SC measures selection, not a strategy.

## Instrument and order of work

`agents/scanner.py` (fetch, context, ask, log), `scripts/scanner_eval.py` (H-SC scorer,
prices from Binance spot 1h klines, which the local mirror also serves), tests in
`test_pipelines.py`. All committed **before** deployment. A local smoke with a fake model
reply, then the owner decides on the VPS cron. The scored window starts at the first
error-free opinion after deployment.

## Addendum 2026-11-16 19:30 UTC: a paper book on the picks (design decision, not a criterion)

Owner's decision, for data yield: every LONG/SHORT pick now also opens a **demo** position in
`scan_positions` (`data/scanner.db`), one per coin, entry at the decision bar's close, stop
1.5 x ATR(1h), target 3 x ATR, closed at the close 24h after entry, a bar touching both
levels counted as the stop, 0.20pp round trip. A 1,000 USD demo account (owner): each position is 10% of realised
equity at entry, opened only if the free cash covers it. Fixed before any position
existed. The model never sees the book, so the picks H-SC scores are unchanged, and **H-SC's
criteria, statistic and dates are unchanged**: it is still scored on `excess` per day from
`scan_picks`, never on this book. The book's P&L is reported beside H-SC and licenses nothing.

Same day, also fixed before any position existed: a second, independent **spot** book on the
same picks (`book = 'spot'`): LONG picks only (spot cannot short), 0.30pp round trip, its own
1,000 USD and one-position-per-coin slots, the same stop/target/24h rule. Like the futures
book it is reported beside H-SC and licenses nothing.

## Addendum 2026-11-17: a count-matched random book (design decision, not a criterion)

Owner's go, for reading the books: a third arm, `data/scanner_random.db`, asks no model. Each
hour, after the pure arm, it takes that bar's pure LONG/SHORT picks (same number, sides and
confidences) and deals them to coins drawn uniformly from those fetched that hour, seeded by
the bar (`random.Random("scanner-random:<bar>")`), so every cycle can be reproduced. A pure
cycle that errored or is missing is recorded as an error row in the random DB. It trades its
own futures and spot books under exactly the rules above. Fixed before any random position
existed.

Why: H-SC's statistic already subtracts the universe mean, but the dollar books do not. A
book that is up in a rising market says nothing about selection. The random book is the
book-level counterpart of the universe mean, and it is what the dashboard sets beside
Claude's books. **H-SC's and H-SC2's criteria, statistics and dates are unchanged.** The
random book is reported only and licenses nothing; `scanner_eval.py --db
data/scanner_random.db` gives its own excess figure for reference.

## Addendum 2026-11-18: prompt v2, so H-SC and H-SC2 restart

The owner asked to cut the API cost while keeping the model (`claude-opus-5-5`, effort low)
and the hourly cadence. The prompt changes only in **how the same numbers are written and
how the answer is shaped**. This prereg says "a new prompt ... would be a new test", so the
change is treated as one.

- **Input:** the same fields and the same coins, written as a CSV table (column names once,
  one row per coin) instead of indented JSON, rounded to 2 decimals (funding_pct 4,
  basis_pct 3). No field is removed. The stored `context` is the rounded data and carries
  `"prompt": "v2"`.
- **Reply:** a JSON object holding only the coins picked LONG or SHORT, each with
  confidence and a reason. A coin left out is NONE, and `{}` is NONE for all. Before this,
  every coin carried a reason, NONE included. Side still comes before reason, so dropping
  the reasons for NONE removes no text that came before a decision.
- **System prompt:** the same task, field definitions and instruction to beat a random coin
  in the same direction, reworded for a table and the shorter reply. The hybrid arm's extra
  sentence is unchanged apart from "row" in place of "coin".
- **Measured before deploy** on the 2026-11-17 17:00 bar, one live call per arm into
  temporary databases (not scored). Pure: input 2,998 → 1,349 tokens, output 614 → 225,
  $0.0243 → $0.0099. Hybrid: $0.0269 → $0.0134. Both replies were well-formed. That one bar
  is a format check only. Its picks overlapped v1's on two of three coins, which is no
  evidence either way: v1 itself repeats only 55% of its LONG/SHORT picks hour to hour.

**Windows.** H-SC (90 days and 300 picks) and H-SC2 (60 paired days) count **only v2 cycles**,
from the first v2 cycle on the VPS. `scanner_eval.load` keeps only cycles whose context says
`"prompt": "v2"`. The ~2 days of v1 cycles stay in the databases and are never scored. The
demo books are not reset; their figures are descriptive and cross the switch. The random arm
follows the pure arm's picks, whichever prompt made them. Nothing else in the criteria
changes.

## Addendum 2026-11-19: book levels on the 24h scale (design decision, not a criterion)

Owner's go. The paper books' stop and target are now 1.5 × and 3 × ATR(1h) × √24
(`BOOK_ATR_SCALE`), for picks opened from the deploy on. Positions opened before keep their
levels. Why: on the 1h scale, one day's noise (about 4.9 × ATR(1h)) took out most stops
within hours, so the books measured a few hours' path of picks made for 24h. Over
2026-11-16..19, 30 of 32 closed positions hit a level before 24h. The books stay
descriptive. **H-SC's and H-SC2's statistic, criteria and dates are unchanged:** they score
`scan_picks` on the close-to-close 24h return, which never read the books.

