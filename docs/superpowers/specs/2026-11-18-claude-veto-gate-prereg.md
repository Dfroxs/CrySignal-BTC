# Pre-registration: would a Claude veto gate improve the BTC bot? (H-VG, forward only)

**Written 2026-11-18, before any figure from the window below has been computed.** The
window starts 2026-11-17 00:00 UTC, after H-S was scored on 2026-11-16. The dashboard's
"BTC + saran Claude" card applies the same veto to *actual* paper positions. No position
has opened since 2026-11-17, and the card is not a statistic of this test. At writing, the
window holds 5 opinions (a count only). No verdict, P&L or episode effect has been read.

## Why

H-S (run 3) passed: Claude's AGREE opinions led to better trades than its DISAGREE opinions,
+3.206pp, 90% CI [+2.015, +4.425]. The results doc lists the caveat that the 20 AGREE
opinions are only four to six episodes. With episodes as the resampling unit, the lower bound
falls to -0.004 to +0.359, and dropping the single largest episode cuts the difference to
about +0.5pp.

H-S licensed exactly this: "pre-registering, for run 4, a test of Claude as a veto gate".
H-VG fixes H-S's two weaknesses:
- the unit is the **episode**, not the hourly opinion;
- the statistic is **what a gate would do to the trade**, not the AGREE-vs-DISAGREE spread.

## The gate (fixed now)

The bot opens a signal only when Claude's opinion on that signal is **not DISAGREE**. An
errored or missing opinion **fails open** (the signal is taken), matching the dashboard
card.

## Data and unit

- **Source:** `shadow_opinions` in `data/signal_history.db`, provider `anthropic`. Stamps
  must satisfy **2026-11-17 00:00 UTC ≤ t < 2027-02-15 00:00 UTC**. Spot and futures are
  pooled, each with its own episodes.
- **Episode:** a maximal run of opinions with the same mode and signal direction, consecutive
  gap < 12h (`shadow_cluster.episodes`, the grouping H-S's follow-up used). Episodes are
  formed from window opinions only.
- **Trade P&L:** each opinion's signal replayed under the bot's own exits per mode
  (`shadow_eval.trade_outcomes`, `backtest._simulate_forward`): stored stop/target, trailing,
  hold cap, costs as configured at scoring time.
- **Per episode:**
  - ungated = the first opinion's trade;
  - gated = the trade of the first opinion that is not DISAGREE, or 0.0 when Claude
    disagreed with every signal in the episode;
  - **effect = gated − ungated**.
- An episode in which either trade cannot be replayed (missing bar, still open at the data's
  end) is dropped, never filled.

## Statistic and criteria

The statistic is the mean effect per episode, with a 90% CI from a bootstrap over episodes
(5,000 draws, seed 2026).

- **PASS:** mean > 0 and CI lower bound > 0.
- **FAIL:** mean ≤ 0.
- **INCONCLUSIVE:** otherwise. It is also INCONCLUSIVE in any of these cases:
  - fewer than **30 episodes**;
  - the gate acted (first opinion DISAGREE, not errored) in fewer than **10 episodes**;
  - more than 20% of window opinions errored.

**Power, stated now.** H-S's window, about five weeks, gave 42 episodes at the 12h grouping,
so roughly 100 are expected here. Most episodes start with DISAGREE: 93 of H-S's 113 opinions
were DISAGREE. The gate will therefore act often, and "no trade" will often replace a trade.
A gate that only removes trades helps exactly when the removed trades lose on average.

## Simplifications, stated now

- Each episode yields one trade under each rule. The replay ignores re-entries after an
  in-episode trade closes and ignores the bot's other gates. It measures the gate on the
  **signal stream**, not on the paper book.
- Exit opinions (close/hold on open positions) are not part of H-VG.

## Window and restarts

The window survives bot restarts that leave the trading path unchanged. A restart that
changes signals, gates or exits (run 4) **ends the window at that moment**. H-VG is then
scored on the opinions before it, under the same power guard. For that reason the run-4
restart is planned for on or after 2027-02-15 (`PAPER_RUN.md`, run-4 plan).

## Reported regardless, never criteria

- per-mode episode counts and mean effect;
- mean ungated P&L by the episode's first opinion (AGREE / DISAGREE / error);
- the signed 24h return per opinion;
- the error share;
- the dashboard card's figures.

## What the outcomes license

- **PASS:** the owner may decide to wire the gate into a later run, shadow to live. That
  overrides the standing rule from the run-3 plan that an LLM is never a live trade gate,
  which is the owner's call, written up as such. Even then it ships with the opinion
  provider's failure mode fixed (fail open), cost and latency budgeted, and its own
  forward check.
- **FAIL:** the veto idea is dropped. The "BTC + saran Claude" card comes off the dashboard,
  or is labelled as a failed idea.
- **INCONCLUSIVE:** nothing ships. The card stays labelled descriptive.

## Discipline

- Instrument: `scripts/veto_gate_eval.py`, committed with this file. It refuses to score
  before 2027-02-15.
- No episode effect, AGREE/DISAGREE spread or replayed P&L on window opinions is computed
  before then. Health checks are allowed: opinion counts and error share, which the daily
  report already prints.
- Scored once. It is never re-scored with a longer window or another grouping to rescue a
  result.

## Amendment 2026-11-18: the window starts at the run-4 restart

The owner chose to ship run 4's correctness fixes now rather than on 2026-11-15+90 days:
futures fee 0.05%, stop before flip, the HTF conflict penalty applied last, the session hour
taken at the bar's close, and a macro HOLD clearing its levels. Those change the bot's
signals. Per § Window and restarts that would end H-VG's window. It is one day old and holds
five opinions, so it is **restarted** instead. No figure was computed on those five, and
none ever will be.

- **Window:** opinions stamped from the run-4 restart (the new manifest's `started_at`) to
  that time plus 90 days. `veto_gate_eval.FROM` and `SCORE_BY` are set to those two
  timestamps in the deploy commit, before any window opinion exists. **Set: run 4 started
  2026-11-17 18:44:50 UTC at `3377027`, so the window is 2026-11-17 18:44:50 to
  2027-02-15 18:44:50 UTC.**
- Everything else is unchanged: the gate, the episode unit, the statistic, the criteria,
  the power guard and the discipline.

## Amendment 2026-11-19: the window restarts at the F&G-greed restart

`integrate_news_with_signal` lost its −0.5 BUY penalty at F&G ≥ 70 (H-FG2 FAIL, owner:
ship now). That changes signals, so per § Window and restarts the window restarts. It was
about 1.5 days old and nothing in it was computed.

**New window:** 2026-11-19 01:13:53 UTC (the bot's restart at `9a3d271`) to 2027-02-17
01:13:53 UTC. Everything else is unchanged.

## Amendment 2026-11-19 (2): the window restarts at the re-entry run-anchor restart

The re-entry gate now ignores an anchor opened before the run's `started_at` (PR #61, a
design decision for data yield). That changes a gate, so per § Window and restarts the
window restarts. It was about 15 hours old and nothing in it was computed.

**New window:** 2026-11-19 16:34:53 UTC (the bot's restart at `8ca245a`) to 2027-02-17
16:34:53 UTC. Everything else is unchanged.

## Amendment 2026-11-20: the window restarts at the resting-orders restart

Live stops and targets now rest like exchange orders: the 1m bars since the last check are
walked first (PR #64, a design decision). That changes exits, so per § Window and restarts
the window restarts. It was about 11 hours old and nothing in it was computed.

**New window:** 2026-11-20 03:22:51 UTC (the bot's restart at `f7310d5`) to 2027-02-18
03:22:51 UTC. Everything else is unchanged.

