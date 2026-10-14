# Results: do spot BUYs at the top of their recent range lose? (H-RT)

Scored against `2026-10-14-range-top-prereg.md` (`c632806`). Instrument
`scripts/range_top_ic.py` committed at `174ec84` before the scored run, with a smoke run
that produced counts only. Raw output: `2026-10-14-range-top-run/run.txt`, `result.json`.

**Discard checks passed.** The universe reproduces H-SR's `kept` arm exactly: n = 347 at
−0.165pp (primary) and n = 43 at −0.515pp (confirmation). The squeeze flag fired on 68
trades in the universe. No 18-bar range was flat.

## Verdicts

| | window | flagged | unflagged | unflagged − flagged [90% CI] | verdict | H-RT3 (vs random flagged bars) |
|---|---|---|---|---|---|---|
| **H-RT1** range top | primary | n=210, −0.241pp | n=137, −0.050pp | +0.191 [−0.728, +1.149] | INCONCLUSIVE | INCONCLUSIVE (random −0.405) |
| | confirmation | n=24, −0.939pp | n=19, +0.021pp | +0.961 [−1.173, +3.159] | INCONCLUSIVE | FAIL (random −0.879) |
| **H-RT2** compound | primary | n=5, −2.694pp | n=342, −0.128pp | — (n < 30) | INCONCLUSIVE | INCONCLUSIVE |
| | confirmation | n=1, −0.808pp | n=42, −0.508pp | — (n < 10) | INCONCLUSIVE | INCONCLUSIVE |

**Overall: H-RT1 INCONCLUSIVE, H-RT2 INCONCLUSIVE.** Per the pre-registration, neither
is licensed. Nothing changes, and no other range-position cut-off may be tried on these
windows.

## What it says

- **The engine is a range-top buyer by construction.** 210 of the 347 trades live would
  have opened (61%) sat in the top 30% of their 72h range, and 147 sat in the top 20%.
  That fits a scoring that rewards price above EMA200, above VWAP and above the BB middle.
  Run 3's three losses were typical entries, not unusual ones.
- **Range-top trades did worse in both windows, but not reliably.** The difference has
  the gate's sign in both windows (+0.19pp, +0.96pp) and its interval spans zero in
  both. That is the same shape as `sr_first` and `psy_sl_first`: a lean, no evidence.
- **A gate here would cost most of the data.** "No first spot BUY in the top 30%" would
  have removed about 60% of spot entries, against a run-3 goal of more data, for an
  effect that cannot be told from zero.
- **BTC alone looks different from the pool, and flips.** Primary: flagged −0.59pp
  (n=68, interval just below zero) against unflagged +0.57pp (n=30). Confirmation:
  flagged −0.29pp (n=5) against unflagged −1.54pp (n=3). This is descriptive and
  carries no verdict, and the flip is the familiar pattern in this repository.
- **The compound pattern is rare.** Range top + squeeze + low volume occurred 6 times in
  ~8 years across 10 symbols. All 6 lost (−2.69pp in the primary), but 6 trades cannot
  license a gate, and live would almost never meet it.
- **The quintile table is not monotonic** (primary: −1.72, −0.75, +0.37, +0.44, −0.57pp
  from bottom to top). The middle of the range did best and both extremes did worst. No
  single cut-off describes that, which is why this table carries no verdict.
- **TP1 above the range is the norm, not the anomaly.** TP1 sat above the 72h high on
  100% of flagged trades and on 80–84% of unflagged ones. TP1 was hit on 8–21% of trades
  in every arm. The exit geometry that #5–#7 met is what nearly every spot trade meets.
  It is untested here and, per the pre-registration, needs its own test.

## What is licensed

Nothing. Run 3's losses #5–#7 are recorded as one losing week in a chop range, bought
where this engine usually buys. The run-4 backlog gains no gate. It notes the TP1-hit
rate (8–21%) as an open question for a separately pre-registered exit test, keeping in
mind that spot exit tweaks failed on 09-21.
