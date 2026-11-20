# Pre-registration: does the engine's score help Claude pick coins? (H-SC2, forward only)

**Written 2026-11-16 (UTC evening), before the hybrid arm, its code or any of its opinions
exist.** Companion to H-SC (`2026-11-17-claude-scanner-prereg.md`), whose pure arm has run
since 2026-11-16 19:04 UTC and is not changed by this.

## Question

Claude picks coins hourly from raw numbers (H-SC, "pure"). Does adding the SpotSignal engine's
own score per coin to the same numbers make its picks better ("hybrid")?

## The prior, stated

The engine loses on altcoins (H-FLP Arm D: BNB -75.9pp, LINK -148.7pp, XRP -218.7pp over
2022-2026), and its components carry no stable predictive power (STEP 1). I expect hybrid to
be no better than pure, possibly worse if Claude follows the score. It is registered because
the comparison is cheap, concurrent and clean, and it answers whether the engine adds anything
in Claude's hands.

## Design (fixed now)

- **Concurrent arms, same data.** One process per hour fetches the nine coins once, then asks
  Claude twice: pure (unchanged prompt and input) and hybrid. Same coins, bars, model
  (`claude-opus-5-5`, effort low), output format.
- **Hybrid input** = the pure input plus, per coin, the engine's futures-mode score on the last
  closed 1h bar: `engine_buy_score`, `engine_sell_score`, `engine_threshold`, `engine_signal`
  (BUY/SELL/HOLD). Computed with `signals.engine.generate_signals` on the coin's last 499
  closed 1h perp bars (`backtest._live_indicators`), support/resistance as live, and 4h / 1d
  HTF from the coin's own perp klines. Market-structure inputs (funding, L/S, OI, basis
  conditions) are passed as absent, because the live fetchers are BTC-only; the raw values are
  already in the shared input. A coin whose engine score fails is passed with nulls.
- **Hybrid system prompt** = the pure one plus one sentence describing the engine fields, and
  that they come from a rule-based system whose record on altcoins is poor (stated to the model
  so it is not presented as authority).
- Own database `data/scanner_hybrid.db`, own demo books (futures and spot, same rules as the
  pure arm's, addendum of H-SC).

## Criteria (H-SC2)

Per arm, per UTC day: the H-SC day value (mean `excess = d x (r - m)` of that day's LONG/SHORT
picks, 24h horizon). **Paired days** = days on which both arms have at least one pick.
`diff = hybrid - pure` per paired day. Moving-block bootstrap over paired days, block 7,
5,000 draws, seed 2026, 90% CI.

- **PASS:** mean `diff` > 0 and CI lower bound > 0, with at least **60 paired days**.
- **FAIL:** mean `diff` <= 0 at or after 60 paired days.
- **INCONCLUSIVE:** otherwise, and always below 60 paired days.
- Health: an arm with more than 20% errored cycles in the window makes it INCONCLUSIVE.
- Scored once at the first scoring date with 60 paired days; never re-scored to rescue.

Reported, never criteria: each arm's own H-SC statistic; pick counts and LONG/SHORT mix; how
often hybrid's pick agrees with `engine_signal`; both arms' demo books; cost.

## What each verdict licenses

- **PASS:** keeping the engine score in the scanner's input for any later scanner work.
- **FAIL / INCONCLUSIVE:** the scanner stays pure. No profit claim either way.

## Addendum 2026-11-20: window restarts at 2026-11-21 00:00 UTC (owner)

With the run-5 reset, H-SC2 counts only cycles of both arms with `bar_close >=
2026-11-21T00:00:00+00:00` (`agents.scanner.WINDOW_FROM`). It needs 60 paired days from
there. The hybrid books restart at $1,000 the same way as the pure ones (see the H-SC
prereg's addendum of this date). Nothing on the window had been computed. Criteria unchanged.
