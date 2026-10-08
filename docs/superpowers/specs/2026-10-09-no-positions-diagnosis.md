# Diagnosis — why the paper run has opened nothing since 2026-09-12

A description, not an experiment. Nothing was pre-registered and nothing here licenses a
change to run 2. Source: a `sqlite3 .backup` snapshot of the VPS database taken
2026-10-08 ~19:55 UTC (`integrity_check: ok`, 1,191 cycles, 2026-08-30 → 2026-10-08), and
a `backtest.py --gates` replay of the same window. Raw backtest output is in
`2026-10-09-no-positions-run/`.

## The question

Spot's last position opened 2026-09-12. Futures has never opened one, across run 1 and
run 2. Is the bot broken, or doing what its code says?

**It is doing what its code says.** No errors, no missing data, no cycles without futures
data. Two gates, each behaving as written, account for every signal since 2026-09-19.

## The funnel, per run

| run | mode | cycles | cleared score | vetoed (`⛔`) | fired | fired ≥ 1.2× | mean ratio (fired) |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | futures | 857 | 254 | 8 | 29 | 4 | 1.117 |
| 1 | spot | 219 | 114 | 5 | 30 | 16 | 1.248 |
| 2 | futures | 92 | 43 | 35 | 8 | 2 | 1.141 |
| 2 | spot | 23 | 13 | 12 | 1 | 1 | 1.264 |

Run 1's veto count is low because its `reasons[:10]` truncation dropped the `⛔` lines;
run 2's figure is the first one that can be trusted. Run boundary: 2026-10-04 07:37 UTC.

## Where fired signals die: `signal_blocks`

Phase 3 writes every rejected signal to the `signal_blocks` table. No ops note mentioned
it before this one, and it answers this question directly:

| run | mode | gate | n |
|---|---|---|---:|
| 1 | futures | `confidence_first` | 25 |
| 1 | futures | `fakeout_first` | 2 |
| 1 | futures | `trend_confluence`, `regime_counter` | 1 each |
| 1 | spot | `stale_cache` | 87 |
| 1 | spot | `reentry_first` | 10 |
| 1 | spot | `pyramid_confidence` | 7 |
| 1 | spot | `regime_bearish` / `fakeout_first` / `confidence_first` / `pyramid_min_distance` | 4 / 3 / 2 / 1 |
| 2 | futures | `confidence_first` | 6 |
| 2 | futures | `fakeout_first` | 2 |
| 2 | spot | `stale_cache` | 3 |
| 2 | spot | `reentry_first` | 1 |

**Read this table with one limit in mind.** The first-entry gates in `run_bot.py` are an
`elif` chain (spot: `reentry → confidence → fakeout → psychology SL → S/R → regime →
trend confluence → breakout chase`). Only the **first** gate to reject is recorded. A
signal blocked by `reentry_first` might also have failed every gate after it; this table
cannot say.

`stale_cache` is not a loss. Spot analyses on 4H bars and replays the cached verdict in
the hourly cycles between them; Phase 3 deliberately refuses to act on a replay. 90 rows
are the same handful of 4H signals repeated.

## Spot: the re-entry guard has no time limit

`run_bot.py:_check_reentry_quality` compares a new signal with the **last WIN or LOSS in
the same direction, with no age limit**. In the whole history of this database spot has
exactly one such position: #3, entered **$77,361** on 2026-09-12 at strength 5.5, closed as
a WIN on 2026-09-15. (#1 and #2 closed `MACRO_CLOSE` and are not benchmarks.)

A new BUY is allowed only if one of these is true:

1. its entry is at or below $77,361,
2. its confidence tier beats the old one (re-derived at today's threshold), or
3. its strength is ≥ 5.5 + 0.3 = **5.8**.

BTC traded $80.7k–$86.7k through run 2, so (1) was never true, and (2) needs STRONG:
≥ 1.5× threshold **and** the 1D HTF agreeing. Every spot signal from 2026-09-19 to
2026-10-07 was blocked here, **11 in total**:

| time (UTC) | strength | tier | entry |
|---|---:|---|---:|
| 09-19 12:01 | 4.25 | WEAK | $81,305 |
| 09-21 04:01 | 5.00 | WEAK | $81,425 |
| 09-24 16:01 | 4.25 | WEAK | $84,275 |
| 09-25 16:01 | 4.50 | WEAK | $83,910 |
| 09-27 20:01 | 5.50 | NORMAL | $84,750 |
| 10-01 16:01 | 4.00 | WEAK | $84,196 |
| 10-02 16:01 | 4.00 | WEAK | $85,358 |
| 10-02 20:01 | 4.25 | WEAK | $84,371 |
| 10-03 16:01 | 4.75 | NORMAL | $84,867 |
| 10-03 20:01 | 4.75 | NORMAL | $84,851 |
| 10-07 04:01 | 5.75 | NORMAL | $84,150 |

The seven WEAK ones would have died at `confidence_first` next anyway. The **four NORMAL
ones** were stopped by this guard first, and what the later gates would have done to them
is unrecorded. The 10-07 signal fell short of the 5.8 bar by **0.05**.

The anchor never ages. In a market that trends away from the last winning entry, the guard
closes spot entirely until price returns or one signal clears STRONG. It does not apply to
futures, which has no resolved WIN/LOSS.

## Futures: the dead zone, then the wick gate

Run 2 fired 8 futures signals:

- **6 were WEAK**, between 1.0× and 1.2× threshold, so they fired but could not open. This
  is the dead zone already measured in run 1 (`2026-10-04-deadzone-*`).
- **2 were NORMAL**, and both were blocked by `fakeout_first`:
  - 2026-10-07 19:01, ratio 1.38: upper wick 78% of the 24H range, rejection from $85,683.
  - 2026-10-08 11:01, ratio 1.26: upper wick 69%, rejection from $83,838.

## Backtest, same window

`backtest.py --gates --start 2026-08-30 --end 2026-10-09`, closed bars, market structure
NEUTRAL:

| mode | signals | closed | W / L | total P&L | taken per trade | blocked per trade (n) |
|---|---:|---:|---|---:|---:|---:|
| spot | 2 | 2 | 1 / 1 | +0.02% | +0.009% | +0.575% (7) |
| futures | 2 | 2 | 1 / 1 | +1.73% | +0.866% | −0.102% (14) |

**These figures support no conclusion.**

- Two trades per mode is not a sample.
- The gate counterfactual points opposite ways in the two modes: spot's gates threw away
  better trades than they kept, while futures' gates kept better ones. Each is drawn from
  7–14 signals. That is the same sign-flipping STEP 1 found.
- The backtest cannot reproduce live trade by trade. Run 1 scored the forming bar, and the
  backtest is blind to 7.5 of the 26.5-point futures ceiling and 3.5 of the 22.5-point spot
  ceiling.

The backtest also opens a few trades in the window where live opened none. That says the
two paths diverge. It does not say either is right.

## What this means

- **No fault.** The silence is the gate stack working as coded, on a market that rose
  ~8% away from the last winning entry.
- **The re-entry guard's missing time limit is a structural defect, not a matter of
  taste.** A guard meant to stop chasing a recent entry has turned into a permanent price
  floor. It is the strongest candidate this run has produced for the next pre-registered
  test, alongside the dead zone and the controller counting fires.
- **Nothing changes in run 2.** Any fix has to be pre-registered and tested on data that
  has not been looked at, per CLAUDE.md. A natural form: give the anchor an age limit, or
  re-base it on the most recent close of any kind, then compare `kept − rejected` against
  the current guard the way `2026-09-24-gates-holdout-*` did.
