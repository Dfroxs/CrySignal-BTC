# Pre-registration: the macro-event rule (H-MC) and the Fear & Greed overlay (H-FG)

**Written 2026-11-19, before any figure below has been computed.**

Checks made before writing:
- **Fear & Greed:** the history was fetched as row counts only: 3,209 days, 2018-02-01 to
  2026-11-18.
- **Calendar:** the ForexFactory calendar archive (Hugging Face `Tropstan/Forex_Factory_Calendar`,
  MIT, 2007-01-01 to 2025-04-04) was checked for structure only:
  - columns;
  - event counts;
  - USD high-impact event names;
  - the weekday of NFP (100% Fridays), FOMC (96% Wednesdays) and claims (96% Thursdays).
- No return, P&L or correlation has been read against either source.

The owner asked whether the backtest accounts for macro events and news. It does not:
`backtest.py` never applies `integrate_news_with_signal`, so these live behaviours have never
been tested:
- the macro hold and force-close;
- the Fear & Greed strength overlay;
- the news-sentiment overlay.

Headline history does not exist; it is archived from 2026-11-19, so that overlay waits for its
own forward test. The other two can be tested on history, and this file fixes how.

---

## H-MC: does the macro-event rule help?

### The rule as live runs it

`signals/sentiment.check_upcoming_macro_events`: a **High-impact USD** event at most 2 hours
ahead, checked at each cycle (the bar's close):
- forces every new signal to HOLD (`integrate_news_with_signal`);
- force-closes every open position at that price (`trading/paper.check_and_close_positions`,
  outcome MACRO_CLOSE).

In run 3, 3 of 11 futures closes were MACRO_CLOSE.

### Event times (fixed now)

The archive's **dates** are right, but from 2012 on most rows carry no time, only 00:00 local.
So each event takes its date from the archive and its time from the official release
schedule (America/New_York, DST applied), then converts to UTC:

- **08:15:** ADP Non-Farm Employment Change.
- **08:30:**
  - Unemployment Claims, Unemployment Rate, Non-Farm Employment Change, Average Hourly
    Earnings m/m;
  - CPI m/m, CPI y/y, Core CPI m/m, PPI m/m, Core PPI m/m, Core PCE Price Index m/m;
  - Retail Sales m/m, Core Retail Sales m/m;
  - Empire State Manufacturing Index, Philly Fed Manufacturing Index;
  - Advance / Prelim / Final GDP q/q, Employment Cost Index q/q;
  - Durable Goods Orders m/m, Housing Starts.
- **09:00:** S&P/CS Composite-20 HPI y/y.
- **09:45:** Flash Manufacturing PMI, Flash Services PMI, Final Manufacturing PMI.
- **10:00:**
  - ISM Manufacturing PMI, ISM Services PMI, JOLTS Job Openings;
  - CB Consumer Confidence;
  - Prelim / Revised UoM Consumer Sentiment, Prelim UoM Inflation Expectations;
  - New Home Sales, Pending Home Sales m/m.
- **13:00:** 10-y and 30-y Bond Auctions.
- **14:00:** FOMC Statement, Federal Funds Rate, FOMC Economic Projections, FOMC Meeting
  Minutes.
- **14:30:** FOMC Press Conference.
- **Excluded,** because their time varies: speeches, testimonies, elections, "Fed
  Announcement", Treasury Currency Report. They are counted and reported.

### Signals and replay

- **Signals:** every signal `run_backtest` simulates for BTC futures 1h. This is H-TRL's
  "Arm C" recording (`trail_futures_ic.record_window`), on the engine as live runs it now
  (run 4).
- **Window:** 2022-01-01 to 2025-03-28. Entries end a week before the archive does, so every
  trade's 72-hour life is covered by the calendar.
- **Two replays per signal,** both with `backtest._simulate_forward` and its own exits:
  - **base:** the signal as the backtest has it, with no macro rule;
  - **rule:**
    - if the signal's bar closes inside a window (an event 0–2 h after that close), it is
      suppressed: P&L 0;
    - otherwise it runs with `exit_params={"flip_at": j}`, where `j` is the first bar after
      entry whose close lies 0–2 h before an event. That bar closes the position at its close,
      priced by `_net_pnl`, after the bar's own stop, target and trail checks;
    - with no such bar inside the trade's life, the rule changes nothing.
- **Effect per signal:** d = pnl_rule − pnl_base, in pp of price, with costs. A signal is
  **affected** when d is defined and the rule acted on it (suppressed or force-closed).

### Statistic and criteria

The statistic is the mean d over affected signals. The 90% CI comes from a bootstrap over
**episodes** (runs of the same direction with a gap under 12 h, as in `shadow_cluster`),
5,000 draws, seed 2026.

- **HELPS:** mean > 0 and the CI lies above 0.
- **HURTS:** mean < 0 and the CI lies below 0.
- **INCONCLUSIVE:** otherwise. Also INCONCLUSIVE below **60 affected signals** or **20
  affected episodes**.

Reported, never criteria:
- suppressed and force-closed separately;
- the share of signals affected;
- mean d by event family (jobs, inflation, FOMC, other);
- the excluded-event count;
- the replay's unresolved count.

### What it licenses (run 4 part B, with the bundled restart)

- **HURTS:** remove the force-close and the hold, or the part that hurts if the separately
  reported parts disagree, recorded with its own note.
- **HELPS or INCONCLUSIVE:** the rule stays as it is.

---

## H-FG: does Fear & Greed point the way the overlay assumes?

### The overlay as live runs it

`integrate_news_with_signal`:
- F&G ≤ 20 and BUY: +1.5 strength.
- F&G ≥ 80 and SELL: +1.5.
- F&G ≥ 70 and BUY: −0.5, which can drop the signal to HOLD.
- F&G ≤ 30 and SELL: −0.5.

The premise is contrarian: extreme fear precedes rises and greed precedes falls.

### Data and statistic (fixed now)

- **Data:** the alternative.me daily index, the value stamped at day D (published around
  00:00 UTC). BTC/USDT daily closes from the exchange (`fetch_ohlcv_df`, 1d).
- **Forward return:** from the open of day D to the close of day D+2, so 72 h, the futures
  hold cap. The return starts at D's open, not before the value was known.
- **Window:** 2018-02-01 to 2026-11-15, every day with both values.
- **Buckets:**
  - FEAR: F&G ≤ 20;
  - FEAR30: F&G ≤ 30;
  - GREED: F&G ≥ 70;
  - XGREED: F&G ≥ 80.
- **Excess of a bucket:** its mean forward return minus the mean over all days.
- **CI:** 90%, moving-block bootstrap of days, block 10 (to cover the overlapping 72 h
  returns), 5,000 draws, seed 2026.

### Hypotheses

- **H-FG1 (the BUY bonus and the SELL penalty): FEAR excess > 0.**
  - PASS: excess > 0, the CI lies above 0, and both halves (2018-02-01 to 2022-06-30 and
    2022-07-01 to 2026-11-15) show excess > 0.
  - FAIL: excess ≤ 0.
  - INCONCLUSIVE: otherwise.
- **H-FG2 (the BUY penalty): GREED excess < 0.**
  - PASS: excess < 0, the CI lies below 0, and both halves show excess < 0.
  - FAIL: excess ≥ 0.
  - INCONCLUSIVE: otherwise.
- **Power guard:** a bucket with fewer than 100 days is INCONCLUSIVE.

FEAR30 and XGREED are reported, never criteria. So are the Spearman IC of F&G against the
forward return, and the 24 h and 7 d horizons.

### What it licenses (run 4 part B)

- **Both PASS:** the overlay stays.
- **A FAIL:** remove that leg of the overlay. For H-FG1 that is the +1.5 BUY bonus and the
  −0.5 SELL penalty; for H-FG2, the −0.5 BUY penalty.
- **INCONCLUSIVE:** that leg is unsupported. It is listed as a candidate to neutralise, which
  is an owner decision, and it stays meanwhile.

---

## Discipline

- The instruments are `scripts/macro_rule_ic.py` and `scripts/fng_ic.py`, committed with this
  file before they are run.
- The calendar CSV and the F&G history are saved under `data/macro_hist/` with their
  download date. Neither is committed: the calendar is redistributable under MIT, but large.
- Each test is scored once, on the window above. It is never re-scored on another window or
  table to rescue a result.
- No profit claim either way. These tests decide whether two live behaviours earn their
  place, not whether the bot has an edge.
