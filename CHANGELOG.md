# Changelog

All notable changes to the SpotSignal project.

---

## 2026-08-30 — feat: `analyze.py --db`

The paper run lives on a server; analysis runs on a workstation. `DB_PATH` was
hard-coded, so the only way to read the run's data locally was to `scp` over the
local database — destroying the only copy of whatever it held, and making two
hosts' reports indistinguishable afterwards.

`--db PATH` points the report anywhere. Every non-JSON run now opens with its
source file, size, and the span of cycles it covers, so a report can be
attributed after the fact.

Tests: `--db` redirects the connection and the report names the file; the flag
still defaults to the local database. Suite: 69/69.

---

## v2.9.0 — 2026-08-30

24 commits since v2.8.0. The theme is measurement: most of this release is
about finding out that the tools used to judge the strategy were themselves
wrong, fixing them, and then honouring what they said afterwards — including
when that overturned a conclusion recorded earlier in the same release.

### The backtest was not measuring what it claimed
Ten defects in the harness every tuned parameter had been measured against:

- The futures backtest simulated **~30 days and reported 90**. Binance caps a
  single `fetch_ohlcv()` at 1000 rows without signalling truncation.
- **HTF was structurally disabled.** Resampling the base timeframe cannot
  produce 200 daily bars from 90 days, so spot's 1W trend was NEUTRAL at every
  index and `aligned` was permanently False — zeroing the largest weight after
  divergence. The daily "EMA200" was an EMA50.
- The 24-hour wick window was **swapped between modes** (96h on spot, 6h on
  futures).
- Execution costs were charged **once on TP2 and twice on trailing exits** —
  precisely the trade-off the trailing factors were tuned on.
- Two entry gates live applies were missing, making the harness *looser* than
  live rather than conservative.

### Risk controls that did not control
- **The circuit breaker never measured drawdown.** It read
  `max(0, -cumulative_pnl)`, which reports 0% for an account 18% below its peak
  but still net positive.
- `.env.example` shipped threshold overrides uncommented, so any deployment
  that copied it ran with the adaptive controller switched off entirely, at a
  looser bar than the tuned defaults.
- The spot cache stored its entry by reference, so Phase 3 edits — a
  breaker-forced HOLD, a pyramid's tightened stop — became the base signal
  replayed for the rest of the 4H candle.

### New measurement tooling
`--walk-forward N`, `--costs`, `--gates`, `--disable`, `--all-conditions`,
`--start/--end`, and `scripts/condition_ic.py` with a horizon sweep. Every
condition now emits its own contribution through a read-only checkpoint,
verified inert against 63 candles.

### What the tooling then said
- **Almost nothing in the scoring stack is stable across years.** Tested on
  2024 against 2025 in both modes, `ema200` runs +3.5 then −2.2, `macd` +1.4
  then −4.7; `mfi` and `rsi` flip sign *significantly in both directions*. Only
  `htf` keeps its sign — negative in all four samples.
- **The gates thin rather than select.** Trades they let through are worse per
  trade (−0.799%) than the ones they reject (−0.557%); total loss falls only
  because count does.
- **The pruning experiment failed.** Dropping the nine conditions with no stable
  predictive power made results worse out-of-sample, so `DISABLED_CONDITIONS` is
  empty and scoring is unchanged. The machinery stays for the next attempt.

### Two conclusions recorded in this release were later overturned by it
"The gates are carrying the system" came from comparing sums over unequal trade
counts. "Trend-following works, mean-reversion doesn't" came from two
overlapping 2026 samples and did not survive a genuine out-of-sample test. Both
retractions are in the entries above rather than edited away.

### Deployment
`deploy/setup.sh` and a systemd unit, both written for an unattended
multi-week run: it refuses to proceed unless Binance answers 200 from the host,
installs Python 3.14 through uv without touching the distro interpreter, adds
swap below 2 GB of RAM, records a run manifest **before** anything writes to the
database, and enables the service only after a full verification cycle passes.

### Validation status
**Unvalidated.** The paper run this release exists to enable has not produced a
sample yet. Over 2024 and 2025 the unmodified system is profitable on spot in
both years (+3.71% PF 1.58, +0.55% PF 1.23) and on futures 2024 H1 (+0.64% PF
1.39) — roughly 15 trades in total, far too few to call an edge.

Tests: 67/67, up from 39.

---

## 2026-08-30 — fix: session/regime bumps apply in full again

Closes the fifteenth review finding, the one held back for a decision.

### Fixed
`market_data._get_adaptive_threshold()` already ends with
`max(base - step, t_min)`, so the base it hands the engine is never below the
mode minimum. Re-applying that same floor after the session and regime bumps
therefore discarded every negative one whenever the controller had walked the
base down to its floor — `max(4.0 − 0.25 − 0.25, 4.0)` — which is exactly the
state a lower bar is meant to answer. Only the +0.5 Asia bump ever survived,
leaving the mechanism one-directional.

The mode minimum governs the adaptive **base**. The bumps are a transient layer
above it and now move the effective bar past it:

```
futures  base 4.00  regime −0.25  session −0.25  →  effective 3.50
spot     base 3.00  regime −0.25  session −0.25  →  effective 2.50
```

What remains in the engine is a single absolute sanity floor
(`_ABS_MIN_THRESHOLD = 0.5`): below that a threshold stops being a bar and
becomes an off switch.

This supersedes the per-mode floor added earlier in this branch. That change
fixed a real bug — the floor was hard-coded to 3.0, the *spot* minimum, applied
to both modes — but fixed it in the wrong place, duplicating a clamp the
controller already owns.

### Tests
The three tests that encoded the old contract are replaced by four that encode
the new one: bumps apply in full in both modes, the effective bar is allowed
below the base floor, the sanity floor holds against a pathological override,
and an AST check that the engine no longer references either mode minimum —
text search would trip on the comment explaining why. Suite: 67/67.

---

## 2026-08-30 — fix: remaining code-review findings

Second pass on the 15-finding review. Fourteen are now closed; the fifteenth is
a behavioural question recorded below rather than changed unilaterally.

### Fixed — measurement correctness
- **The gate counterfactual compared a filtered numerator with an unfiltered
  count.** `taken_pnl` excluded OPEN rows while `n_taken` counted them, and
  every OPEN shadow (`_simulate_forward` never returns None — it falls through
  to an `"OPEN"` row at max_hold) padded the blocked denominator with a zero.
  Both sides now filter on a single `RESOLVED` tuple. **Re-measured: the
  earlier conclusion survives** — taken −0.799%/trade vs blocked −0.557%/trade,
  so the gates still thin rather than select. The figures moved (blocked 46 → 47,
  −26.76% → −26.16%) because the trailing fix below changed some outcomes.
- **The backtest trailed futures wider than the bot does.** After TP1 it pulled
  the trail to `entry − 0.5×ATR` in both modes; `trading/paper.py` does that for
  spot only and snaps futures to breakeven. Futures win rate and profit factor
  were systematically overstated, and the trailing factors were tuned against
  those numbers.
- **Per-gate verdict called a gate "COSTS money" before checking redundancy.**
  A gate with `only == 0` changes no trade if removed; that is the more
  actionable fact and now wins the label.
- **`analyze.py` counted infrastructure skips as trading gates.** The rewrite
  dropped the old explicit exclusion while this branch added a `stale_cache`
  block that fires on up to 3 of every 4 cycles. On sample data it took 82% of
  the histogram, pushing both real gates under the display threshold and
  deflating entry conversion. Infrastructure skips are now excluded and
  reported separately.

### Fixed — robustness
- **`--gates` crashed on the period it exists to investigate.** With no taken
  trades `run_backtest` returned `{"blocked": [...]}`, which is truthy, so
  `print_backtest_results` fell past `if not stats` and raised `KeyError:
  'start_price'`. It now guards on a key real stats always carry.
- **A failed HTF fetch aborted the whole backtest.** `_load_htf_series` sits
  outside any try and indexed `d.index[0]` unguarded, so a transient error, a
  geo-block, or a span the exchange has no bars for killed the run — where the
  per-candle try/except it replaced degraded to `htf=None` and continued. It now
  degrades with a warning, which also un-breaks `scripts/backtest_offline.py`:
  that script patches `fetch_ohlcv_df`, and the HTF loader bypasses it by design.

### Changed
- `_htf_at` binary-searches precomputed bar close times instead of rebuilding a
  DatetimeIndex, a boolean mask and a DataFrame slice per candle. The old form
  was O(candles × htf_bars); `scripts/condition_ic.py` pays it once per candle
  across 180–400 days.
- Removed `_simulate_forward`'s dead `ec` parameter — it implied a per-call cost
  model that stopped existing when `_costs()` moved to module scope — and an
  unused `datetime` import left by `_candle_utc_hour`'s deletion.
- `CLAUDE.md`: the documented `generate_signals` signature was missing
  `disabled`; the "thresholds should be ~25–30% of max" rule contradicted
  `config.py`, which now pins 19.6% / 19.1%; `CONDITION_MAX`,
  `DISABLED_CONDITIONS`, `_contributions` and the six new backtest flags were
  undocumented.

### Recorded, not changed
**The per-mode threshold floor makes the negative session/regime bumps inert on
futures.** `get_adaptive_threshold()` already clamps the base at
`THRESHOLD_MIN`, so `mode_min = min(THRESHOLD_MIN, threshold)` is 4.0 whenever
the controller has walked the base down to its floor — and
`max(4.0 − 0.25 − 0.25, 4.0)` discards both the US-session and TRENDING bumps.
Only the +0.5 Asia bump ever survives, so the mechanism became one-directional
at exactly the moment a lower bar is the point. This is a consequence of the
floor fix earlier in this branch; changing it again alters live scoring, so it
waits for a decision rather than being reverted mid-stream.

### Tests
Suite: 66/66. The `_htf_at` tests were updated to the new
`(series, close_times)` frame contract and now also assert that an empty frame
set returns `None` instead of raising.

---

## 2026-08-29 — feat: condition-set machinery; the pruning experiment failed

Option (a) from the review — prune the scoring stack — was implemented,
measured, and **rejected on the evidence**. The machinery stays, the pruning
does not.

### Added
- **`config.CONDITION_MAX`** — the per-condition BUY-side ceiling, keyed by the
  engine's attribution names. It was a comment table; it is data now because the
  ceiling and the thresholds have to move together when the condition set does.
  Verified to reproduce the documented 22.50 / 26.50 exactly.
- **`config.DISABLED_CONDITIONS`** — the active set, honoured by
  `generate_signals(disabled=None)`. `--disable a,b` adds to it for one run;
  `--all-conditions` ignores it.
- **Thresholds derived as a fraction of the ceiling** rather than absolute
  numbers. Pruning lowers the ceiling, and an absolute bar would silently become
  a stricter one — an experiment that changed the condition set would then be
  measuring two changes at once. The fractions reproduce 5.2 / 4.3 / 4.0 / 8.0 /
  3.0 / 7.0 exactly at the pre-pruning maxima, so the mechanism itself moves
  nothing.
- **`backtest.py --start / --end`** so one period can be tested against another.

### The experiment
Hypothesis: the nine conditions with no stable candle-level predictive power
(`htf`, `cmf`, `vwap`, `rsi_divergence`, `support_resistance`,
`effort_vs_result`, `volume`, `candlestick`, `obv`) are dead weight, and
dropping them leaves the same result with far fewer parameters.

First attempt confounded two changes — a smaller condition set *and* a bar that
had moved relative to the score distribution. Signals fell 8 → 2 and the result
looked catastrophic. Isolating it by calibrating the pruned threshold on 2024 to
match the full set's signal count, then applying that threshold unchanged to
2025:

| window | full set | pruned set |
|---|---|---|
| spot 2024 (calibration) | +3.71%, PF 1.58, 8 signals | +2.84%, PF 1.75, 6 signals |
| spot 2025 (out-of-sample) | **+0.55%, PF 1.23**, 7 signals | **−1.33%, PF 0.51**, 5 signals |
| futures 2024 H1 | **+0.64%, PF 1.39**, 5 signals | **−0.82%, PF 0.00**, 1 signal |

The full set wins in all three windows. `DISABLED_CONDITIONS` is therefore
empty and scoring is unchanged.

### What this says
The candle-level measurement and the trade-level one disagree, and both can be
right. Marginal IC asks whether a condition predicts the next N bars *across
every candle*; this system trades on the thin tail where the combined score
clears a bar. A weak, weakly-correlated component can contribute nothing on
average and still improve the ranking at that tail. Ablation over all candles
does not measure the thing the system actually uses.

With ~15 trades across both years, neither result is conclusive — which is the
reason to keep the mechanism and act on neither.

### Correction
Earlier entries described this system as loss-making in every test. That was
drawn from the trailing 2026 window, where it produces almost no signals. Over
2024 and 2025 the unmodified system is **profitable on spot in both years**
(+3.71% PF 1.58, +0.55% PF 1.23) and on futures 2024 H1 (+0.64% PF 1.39). The
sample is far too small to call an edge, but "every test is negative" was wrong
and came from generalising one window.

### Tests
- Thresholds track the active set; `CONDITION_MAX` covers every condition the
  engine scores; `disabled=None` applies the configured set while an empty
  collection scores everything. Suite: 65/65.

---

## 2026-08-29 — fix: gate counterfactual reports per-trade, not just totals

### Fixed
- **The counterfactual's headline number was misleading.** It reported that the
  gates turn a −29.16% ungated result into −2.40%, which reads as the gates
  carrying the system. Comparing totals across different trade counts cannot
  show that. Per trade:

```
             trades     total   per trade
  Taken           3    -2.40%     -0.799%
  Blocked        46   -26.76%     -0.582%
  Ungated        49   -29.16%     -0.595%
```

  **The trades the gates let through are worse per trade than the ones they
  reject.** Total loss fell only because count fell from 49 to 3 — the effect
  any filter of that severity produces on a negative-expectancy system,
  including a random one. The gate set has negative selection value on this
  window, not positive.

  The report now prints per-trade P&L per gate and for each bucket, and says
  explicitly whether the gates are selecting or merely thinning.

### Correction
This retracts the second reading recorded under "feat: entry-gate
counterfactual" — *"the gates are carrying the system, not the score"*. They are
not carrying it. That conclusion came from comparing sums over unequal counts,
which is exactly the error the per-trade column now makes impossible to repeat.

Note also that `confidence_first`, the gate with the most unique blocks, is
itself a threshold on the score (`strength >= threshold × 1.2`), so "keep the
gates, drop the scoring stack" was never a coherent split to begin with.

---

## 2026-08-29 — feat: explicit date ranges + 2024 vs 2025 replication

### Added
- **`signals/ohlcv.py:_fetch_ohlcv_range()`** and `fetch_ohlcv_df(since=, until=)`
  — page *forward* through an explicit span. `_fetch_ohlcv_paged()` walks
  backwards from now, so every sample it can produce ends today and overlaps
  every other one; independent replication was not available at all.
- **`scripts/condition_ic.py --start / --end`**, with HTF warmed from before the
  window opens so the opening weeks are not scored on half-formed indicators.
- `backtest.py:_load_htf_series(start=, end=)` for the same reason.

### Measured — 2024 vs 2025, two modes, non-overlapping years
t-statistic range across horizons; **bold** = |t| ≥ 2 somewhere in the sweep.

| condition | fut 2024 | fut 2025 | spot 2024 | spot 2025 |
|---|---|---|---|---|
| `htf` | **−3.7 → −5.9** | −1.0 → −1.7 | **−2.5 → −3.6** | −0.7 → **−3.8** |
| `cmf` | **−3.1 → −3.0** | **−2.5 → −3.5** | flat | −0.3 → −1.9 |
| `ema200` | +1.8 → **+3.5** | **−2.6 → −2.2** | +0.6 → +1.9 | +0.5 → +1.8 |
| `macd` | +1.5 → +1.4 | **−2.8 → −4.7** | **+2.9 → +2.4** | +0.1 → +1.8 |
| `mfi` | **−2.0 → −5.6** | **+3.2 → +2.1** | **−3.6 → −3.0** | +0.4 → +1.5 |
| `rsi` | −0.8 → **−3.8** | **+2.4 → +2.6** | **−2.1 → −0.9** | +1.2 → +0.6 |
| `adx` | +0.7 → **+5.8** | −1.5 → +0.9 | +1.2 → +1.7 | **−2.5 → −3.6** |
| `extreme_cluster_penalty` | +1.8 → **+3.4** | +0.3 → −1.2 | **+3.0 → +2.2** | **−2.0 → −2.8** |
| `rsi_divergence` | +1.0 → **−3.3** | +0.8 → **−3.0** | −1.1 → **−3.2** | +1.0 → **+3.5** |

**This overturns the reading recorded in the previous commit.** That entry
concluded "the trend-following conditions point the right way and the
mean-reversion conditions point the wrong way", from two overlapping 2026
samples. Across separate years it does not hold: `ema200` runs **+3.5 in 2024
and −2.2 in 2025**, `macd` **+1.4 then −4.7**, and `mfi` and `rsi` flip sign
*significantly in both directions*. The earlier pattern was a property of the
2026 window, not of the indicators. Overlapping samples produced a confident
conclusion that a genuine out-of-sample test destroyed.

What survives:
1. **`htf` is negative in all four samples** (significant in three). It is the
   only condition that never changes sign, and it carries the largest weight in
   the engine after divergence.
2. **`cmf` is significantly negative in both futures years.**
3. **Everything else is regime-dependent.** Several conditions clear |t| ≥ 2 in
   *opposite directions* between years — `extreme_cluster_penalty` +3.0 then
   −2.8 on spot, `rsi_divergence` −3.2 then +3.5. A fixed weight cannot serve
   both years; the question is not what weight to pick but whether a fixed
   weight is the right structure at all.

### Not changed
Still no weight touched. The finding that matters is not "invert `htf`" — it is
that the scoring stack's parameters are being asked to hold constant across
regimes in which the underlying relationships reverse.

### Tests
- Range fetch walks a requested span past the exchange cap without overlap or
  reorder, and terminates at the end of history. Suite: 62/62.

---

## 2026-08-29 — feat: horizon sweep + condition ablation

### Added
- **`scripts/condition_ic.py --horizons 3,6,12,24,48,72`** — every horizon from
  one pass (the engine loop is the cost; forward returns are a shift). A
  condition that predicts something keeps its sign as the holding period moves;
  one that flips is reading a different phenomenon at each scale and a fixed
  weight cannot serve both.
- **`backtest.py --disable htf,mfi,rsi`** — exact condition ablation. It reuses
  the attribution checkpoints: a disabled condition has the accumulators rolled
  back to their pre-condition values, so an ablation run cannot drift from the
  real scoring path and no condition body was touched. Flags a condition sets
  for later blocks (`_rsi_os` and friends) are deliberately *not* unset.

### Measured — the sweep replicates across two samples
FUTURES 1H / 180d and SPOT 4H / 400d, t-statistic by forward horizon:

| condition | futures (3→72b) | spot (3→18b) | reading |
|---|---|---|---|
| `ema200` | +0.4 → **+5.0** | +0.9 → **+2.4** | correct, strengthens with horizon |
| `cmf` | −0.6 → **+5.0** | −0.1 → **+3.3** | correct at longer horizons |
| `adx` | −0.1 → **+3.9** | +1.3 → +1.8 | correct |
| `macd` | +0.7 → **+4.1** | **+2.2** → +0.3 | correct |
| `htf` | **−3.1 → −4.9** | **−2.3 → −6.1** | **inverted at every horizon, both samples** |
| `mfi` | −0.7 → **−4.4** | −1.1 → **−3.0** | **inverted** |
| `rsi` | +0.3 → **−2.4** | −1.3 → **−2.2** | **inverted** |
| `bollinger` | −0.3 → **−4.0** | flat | inverted in futures |

The pattern is coherent: **the trend-following conditions point the right way
and the mean-reversion conditions point the wrong way.** On these samples an
oversold reading was followed by more downside, not a bounce. `htf` is the
outlier — a trend condition that reads backwards, and the most consistently
anti-predictive thing in the engine while carrying the largest weight after
divergence.

Three of the largest weights in the scoring stack — `htf` (2.0), `rsi` (1.5),
`mfi` (1.5) — are anti-predictive on both samples. That is a plausible
mechanism for the counterfactual result in the previous commit, where the
ungated engine produced 49 signals worth −29.65%.

### Ablation, 180-day futures
```
baseline            3 trades   -2.40%
--disable htf       1 trade    -1.14%
--disable htf,mfi,rsi   no signals at all
```
Directionally consistent with the sweep, but n=3→1 settles nothing. That is the
point: the trade-level test cannot resolve this question at 12 signals a year,
which is why the candle-level measurement exists.

### Not changed
No weight was touched. Both samples are BTC and overlap in time, the
observations are autocorrelated, and the era is one regime. The evidence is
strong enough to investigate and not strong enough to re-weight on.

### Tests
- Section 14 — contributions sum to the scores, ablation subtracts exactly one
  condition and nothing else, empty ablation is a no-op. Suite: 60/60.

---

## 2026-08-29 — feat: per-condition predictive power (scripts/condition_ic.py)

### Added
- **`signals/engine.py` per-condition attribution.** `generate_signals()` now
  emits `signal['_contributions']` — the buy/sell delta each numbered condition
  contributed. Implemented as checkpoints that only *read* the accumulators, so
  it cannot alter a score: verified across 63 candles against the previous
  commit with zero differences in `buy_score`, `sell_score` or signal type, and
  the contributions summing exactly to the final scores.
- **`scripts/condition_ic.py`.** The full system fires ~12 times a year and can
  never be validated on its own trade count; its components are evaluated on
  every candle. This scores each condition's contribution against the forward
  return over thousands of observations and reports fire rate, rank IC, mean
  forward return when bullish vs bearish, the spread between them, and a Welch
  t-statistic.

### Measured
FUTURES 1H, 180 days, 6-bar horizon — 4314 candles, baseline drift +0.027%:
```
htf                       100%  IC -0.013   edge -0.138%   t -3.5
obv                       100%  IC -0.052   edge -0.057%   t -1.8
stoch_rsi                  51%  IC +0.039   edge +0.074%   t +1.8
… 13 more, all |t| < 2
Distinguishable from noise: 1/16 — and it is inverted
```
SPOT 4H, 400 days, 3-bar horizon:
```
htf                       100%  IC -0.053   edge -0.142%   t -2.3
macd                      100%  IC +0.017   edge +0.131%   t +2.1
Distinguishable from noise: 2/16
```
Two findings, stated as findings and not acted on:
1. **The HTF condition scores backwards in both runs** — different mode,
   timeframe, period and horizon, same sign, both |t| ≥ 2. It carries the
   largest weight in the engine after divergence (up to +2.0).
2. **Almost nothing else separates from noise.** 14 of 16 measurable conditions
   in futures, 14 of 16 in spot.

The report prints its own caveat: consecutive candles share overlapping forward
windows, so the observations are autocorrelated and these t-statistics are
optimistic. `|t| ≥ 2` means "worth investigating", not "proven".

### Found, not fixed
- **Condition 16 (S/R proximity) can never fire.** `detect_support_resistance()`
  only returns levels at least `tolerance` = 0.5% away from the close, while the
  condition scores only when a level is within 0.2×ATR. Measured over 300
  candles per timeframe: nearest level is ≥0.504% away, the proximity window
  reaches at most 0.270% (1H) / 0.373% (4H). The ranges cannot overlap, in
  either mode — so `✓ Bouncing off support` has never once been printed, and
  both MAX_SCORE constants overstate by 0.75. Fixing it means choosing a new
  parameter (widen the window, lower the detector tolerance, or delete the
  condition); that choice should be measured, not guessed.

---

## 2026-08-29 — feat: entry-gate counterfactual

### Added
- **`backtest.py --gates`** — every signal an entry gate rejects is also
  simulated forward, so each gate can be judged on what it *threw away* beside
  what it saved. Each of these gates was added to erase one specific losing
  trade; that is easy after the fact, and nothing had ever measured the winners
  that went with it.
- `_passes_entry_gates()` became `_failing_gates()`, returning **every** gate
  that rejects a signal rather than the first. With an early return whichever
  gate is checked first absorbs all the credit and the ones behind it look
  inert — useless for deciding what to prune. The report separates `blocked`
  (any gate would have caught it) from `only` (no other gate would have).
  Counterfactual entries obey the same one-position-per-direction rule as real
  trades, so a setup that re-fires for ten candles is counted once.

### Measured — 180-day futures run
```
gate                 blocked  only   won    net P&L  verdict
confidence_first          36     3     5    -22.16%  saves money
trend_confluence          31     0     4    -17.65%  redundant
fakeout_first             32     0     4    -16.77%  redundant
regime_counter            23     1     3    -12.77%  saves money
reentry_first             17     0     1     -9.71%  redundant
psy_sl_first               7     0     0     -4.21%  redundant
sr_first                   1     0     0     -2.00%  redundant

Taken     :   3 trades    -2.89%
Blocked   :  46 trades   -26.76%
Ungated   :  49 trades   -29.65%
```
Two readings worth recording:
1. **Five of seven gates are fully redundant on this window** — 0 unique blocks.
   Removing them would not change a single trade taken. Only
   `confidence_first` (3 unique) and `regime_counter` (1) do work nothing else
   does.
2. **The gates are carrying the system, not the score.** The engine produced 49
   signals; 46 were losers the gates caught. A −29.65% ungated result becomes
   −2.89%. That points at the scoring stack, not the filters, as the part that
   is not earning its complexity.

Neither reading is acted on here — one 180-day window is not enough to prune
on. It is the first evidence either way.

### Tests
- Gate attribution lists every failing gate, with no duplicates. Suite: 57/57.

---

## 2026-08-29 — feat: walk-forward windows + execution-cost sensitivity

Validation tooling, not tuning. Nothing about the strategy changed.

### Added
- **`backtest.py --walk-forward N`** — splits the evaluated period into N
  sequential windows and stats each with the parameters held fixed. A single
  90-day number cannot distinguish an edge from a lucky window; a profit factor
  that crosses 1.0 between windows is noise wearing a result's clothes. Windows
  span the **tested period**, not the first-to-last-trade range, so a stretch
  that produced no signal is reported as `no signal` rather than silently
  dropped — with this system that is most of them.
- **`backtest.py --costs`** — re-prices every trade at other execution-cost
  levels and shows what the current setting consumes. Exact rather than
  approximate: since costs stopped shifting entry/stop/target prices, trade
  selection and exit points no longer depend on them, so a plain trade carries
  2 legs of cost and one that took TP1 carries 1.5.

### Fixed
- **HTF data ran out on longer backtests.** `_HTF_LIMIT` was pinned at 1000
  bars — 166 days of 4H — so a 180-day run lost its 4H trend for the first two
  weeks, the same quiet degradation that made the resampled HTF useless.
  `_htf_bars_needed()` now scales the fetch to the window plus 250 bars of
  EMA200 warmup. On a 180-day futures run this recovered a trade the harness
  had been skipping.
- `print_backtest_results()` printed the `LOOKBACK_DAYS` constant as the period
  regardless of `--days`.

### Measured
180-day futures run, parameters fixed: **3 trades, 0 wins, −2.88%**, spread
across 3 of 6 windows; the other 3 produced no signal at all. Cost sensitivity
on that run: −1.86% gross → −2.40% net at the configured 0.18% round trip.

### Tests
- `test_pipelines.py` section 13 — window span (including empty windows) and
  exactness of the cost re-pricing. Suite: 56/56.

---

## 2026-08-29 — fix: backtest fidelity (10 findings) + real drawdown breaker

A full re-audit of the modules not covered by the earlier passes — persistence,
the loop, notifiers, scraper and the backtest harness — turned up ten defects.
Most of them sit in `backtest.py`, the tool every tuned parameter in this repo
was measured against.

### Fixed
- **The futures backtest simulated ~30 days and called it 90.** Binance caps a
  single `fetch_ohlcv()` at 1000 rows and does not signal truncation, so the
  request for `90 × 24 + 200 = 2360` hourly candles quietly returned 1000
  (42 days of data, ~30 days of simulation after warmup and the max-hold
  margin). `signals/ohlcv.py:_fetch_ohlcv_paged()` now pages backwards through
  the cap. Verified: 2360 candles / 98 days, sorted, no overlap.
- **The backtest's HTF was structurally disabled.** `_compute_htf_from_df()`
  resampled the base timeframe, which cannot hold enough bars: 90 days of 4H is
  ~18 weekly bars against a 50-bar minimum, so the spot backtest's 1W trend was
  NEUTRAL at *every* index and `aligned` was permanently False — condition 6 is
  worth up to +2.0, the largest weight after divergence. The daily "EMA200" was
  an EMA50 for the same reason, and futures' 1D was NEUTRAL for the first half
  of every run. The harness now fetches the same 1D/1W (spot) and 4H/1D
  (futures) series live uses and reads per-bar indicators from
  `signals/htf.py:htf_indicator_series()` — one implementation for both paths,
  verified byte-identical to the previous live output on fixed data. Only bars
  that had already **closed** are read, so the replay cannot see into a forming
  bar.
- **The 24-hour wick window was swapped between modes.** `_passes_entry_gates()`
  used 24 bars on spot (= 96 hours) and 6 on futures (= 6 hours); live uses 6
  and 24 respectively, both meaning 24 hours.
- **Execution costs were charged inconsistently per exit path.** Entry/SL/TP
  prices were shifted by the round-trip cost *and* an exit cost was deducted
  again on the trailing/time/vol paths, while TP2 was charged nothing. That
  flattered every TP2 win and double-charged every trailed exit — precisely the
  trade-off the trailing factors were tuned on. One model now, `_net_pnl()`,
  asserted equal to `trading/paper.py::_calc_pnl` across modes, directions and
  partial states.
- **The backtest skipped two gates live applies.** S/R entry proximity and
  re-entry quality were missing, so the harness passed trades live rejects —
  the opposite direction from the "conservative" disclaimer.
- **The circuit breaker never measured drawdown.** It read
  `max(0, -get_closed_pnl())`, which is cumulative net P&L: an account up 30%
  that gives back 18% reported a 0% drawdown and kept trading through a
  `max_drawdown_pct` of 15. `trading/history.get_drawdown()` walks the
  closed-trade equity curve and returns (current, max) peak-to-trough. The
  equity check is now expressed directly as `100 + total < min_equity_pct`
  rather than via the same conflated quantity.
- **The mid-cycle check could not fire a funding exit.** `run_position_check()`
  called `check_and_close_positions()` without `funding_rate`, so the gate was
  dead outside full cycles and a position could sit through an expensive
  funding window.
- **MACRO_CLOSE left `exit_price` NULL** — the only close path that did not
  record the fill.

### Removed
- `backtest.py:_candle_utc_hour()` — dead since the engine started reading
  `df.index[-1].hour` itself. The indicator import block went with it; every
  name in it was already unused because `fetch_ohlcv_df()` computes the columns.

### Changed
- `trading/history.partial_close_position()` docstring claimed the trail moves
  to breakeven; spot moves it to entry − 0.5×ATR and the caller decides.
- `CLAUDE.md` backtest-limitations section rewritten — it documented the
  resampling approach that caused the HTF defect.

### Effect
Before this commit both backtests produced **zero** signals on the current
window. After it, futures runs a real 90-day window and produces 3 trades
(0W/3L, −2.40%, all SELL). Spot still produces none — consistent with the
stacked-filter question raised in the previous audit, which needs the paper run
to answer.

### Tests
- `test_pipelines.py` section 12 — six regression tests: the per-mode wick
  window, cost parity with the live P&L function, peak-to-trough drawdown, the
  closed-bar-only HTF read, the HTF alignment rule, and pagination past a
  stubbed exchange cap. All six verified to fail against the pre-fix tree.
  Suite: 54/54.

---

## 2026-08-29 — fix: cancelled RSI extreme no longer penalised as a cluster member

### Fixed
- **The diminishing-returns penalty charged for a score that had been
  removed.** The correlated-extreme block ran before condition 7, so when a
  divergence cancelled the RSI OS/OB score (`sell_conditions -= 1.5` /
  `buy_conditions -= 1.5`, "divergence takes precedence") the penalty had
  already counted that RSI flag as a cluster member. The side lost 0.75 for a
  component contributing nothing. Condition 7 now runs ahead of the block and
  clears `_rsi_os` / `_rsi_ob` when it cancels them, so only extremes still
  contributing are counted. Divergence itself is never a cluster member, so
  the reorder does not expose it to the penalty — the reason the block used to
  sit first. Measured on a synthetic bearish-divergence flush (RSI 19.7, MFI
  7.5): spot buy score 1.35 → 2.10, the unwarranted −0.75 removed.

### Tests
- `test_pipelines.py` — `test_cancelled_rsi_extreme_leaves_the_cluster`, on a
  fixture that prints a higher high on weaker RSI and then flushes into
  oversold. Verified to fail against the pre-fix source. Suite: 48/48.

---

## 2026-08-29 — fix: dead config, scoring double-count, stale spot cache, gate analytics

### Fixed
- **`.env` was switching the adaptive threshold off.** `.env.example` shipped
  `SIGNAL_THRESHOLD=5.0` / `SPOT_THRESHOLD=4.0` uncommented, and
  `_get_adaptive_threshold()` returns any env value immediately — so every
  deployment that copied the example ran with a pinned threshold and no
  frequency or win-rate control at all, at a *looser* bar than the tuned
  defaults (5.2 / 4.3). Both lines are now commented out in `.env.example`
  (with the consequence spelled out) and in this repo's `.env`. Also dropped
  `DISCORD_WEBHOOK_URL` from the example — Discord support was deleted in
  2026-05 and nothing reads it.
- **MFI double-counted the price extreme.** The diminishing-returns block
  discounts RSI OS/OB, BB lower/upper and the StochRSI crossover because they
  all fire off the same extreme, but MFI ≤20 / ≥80 (±1.5) sat outside the
  cluster and stacked on top. Condition 20 now runs ahead of the block and
  contributes `_mfi_os` / `_mfi_ob`; the penalty scales to 4 members
  (2→−0.75, 3→−1.5, 4→−2.25). Measured on a synthetic flush with RSI 6.2 and
  MFI 0.0: spot buy score 3.60 → 2.85. **This changes scoring** — fewer and
  later entries at extremes — and wants re-validation on fresh data.
- **Spot entries could open at a 3h-old price.** `analyze_spot_signal()` caches
  its result per 4H candle, so with `--loop 60` three of every four cycles
  replayed a stale `entry_price` and stale gate inputs into Phase 3. Cached
  results are now flagged `_cached` and Phase 3 records a `stale_cache` block
  instead of opening; display, notifications and exit management are unaffected
  (exits already run off the live price fetched in `run_cycle()`).

### Changed
- **`analyze.py` entry-gate analysis reads the database.** `section_skip_gates()`
  regex-matched `spotsignal.log` against a hand-maintained pattern table, while
  `run_bot._block()` has been writing structured rows to `signal_blocks` (indexed
  on `gate` and `(mode, gate)`) that nothing queried. It now groups that table by
  gate, respects `--mode`, shows a representative reason for each gate above 10%,
  and derives entry conversion from `paper_positions`. New gates appear
  automatically instead of falling into "other:".
- **`SIGNAL_MAX_SCORE` 25.75 → 26.50, `SPOT_MAX_SCORE` 21.75 → 22.50.** These are
  display denominators only (the "score X / max" line). Both had drifted from the
  weights actually in `generate_signals()`. `config.py` now carries the per-block
  derivation table so the next condition change can be checked against it.

### Removed
- **`RISK_CONFIG["max_positions"]` and `FUTURES_CONFIG["max_positions"]`** — never
  read by any module, and both descriptions were wrong. Spot is capped by
  `pyramid.max_entries` (3); futures holds at most **one** position at a time,
  because `run_bot.py` closes an opposite position before opening (close-and-flip)
  and skips a same-direction one. `CLAUDE.md` corrected — it claimed 2 futures
  positions (1 LONG + 1 SHORT), a state the code cannot reach.
- **Unused `SPOT_THRESHOLD` / `SPOT_MAX_SCORE` import** in `signals/spot.py`.

### Tests
- `test_pipelines.py` section 11 — MFI joining the correlated-extreme cluster,
  the stale-cache flag on a replayed spot analysis (and that the stored copy
  stays unflagged), and a guard that Phase 3 consults the flag. Verified to fail
  against the pre-fix sources. Suite: 47/47.

---

## 2026-08-29 — fix: threshold propagation, HTF confidence, per-mode floor

### Fixed
- **The threshold the engine gated on was thrown away.** `generate_signals()`
  stores the *effective* threshold in `signal['_threshold']` — adaptive base
  plus the regime bump and the session bump. `signals/spot.py` and
  `signals/futures.py` then overwrote that key with the raw adaptive base
  immediately after the news overlay, so every downstream consumer measured
  strength against a number the engine never used:
  `sizing._compute_confidence()` (factor 1 is `strength / _threshold`, and it
  drives leverage), `run_bot._check_reentry_quality()` (confidence-tier
  comparison), the Telegram/terminal "≥ thr" line, and the `threshold` column
  of `cycle_log`. In a RANGING regime during the Asia session the two values
  differ by up to 1.0. Both pipelines now leave the engine's value alone.
  Note for analysis: `cycle_log.threshold` (and therefore `analyze.py`'s
  ADAPTIVE THRESHOLD DRIFT section) now records the effective threshold, so
  drift includes session/regime bumps, not just adaptive movement.
- **The news overlay silently promoted signals back to STRONG.**
  `get_signal_confidence()` takes `htf` / `signal_type` so that STRONG requires
  the 1D timeframe to agree with the direction (audit #9 — STRONG scored 12.5%
  WR vs NORMAL 40% because top-of-move signals score highest).
  `integrate_news_with_signal()` recalculates confidence after adjusting
  strength but called it with two arguments, dropping the HTF rule and
  re-upgrading a signal the engine had deliberately downgraded to NORMAL. The
  counter-trend block already rejects directions that oppose a non-NEUTRAL 1D,
  so the live exposure was the HTF-fetch-failure path (`1d = NEUTRAL`) — where
  a promoted STRONG unlocks spot pyramiding, whose `min_confidence` is STRONG.
  `integrate_news_with_signal(signal, news_data, htf=None)` now accepts the HTF
  dict and forwards it; both pipelines pass it.
- **Futures could gate below `THRESHOLD_MIN`.** The floor applied after the
  regime and session bumps was hard-coded to `3.0` — the *spot* minimum —
  for both modes, so futures in a TRENDING regime during the US session gated
  at 3.5 (4.0 − 0.25 − 0.25) despite `THRESHOLD_MIN = 4.0`. The floor is now
  per-mode (`SPOT_THRESHOLD_MIN` / `THRESHOLD_MIN`). An explicit env override
  that already sits below its mode minimum is still honoured — the floor
  guards the bumps, not the operator's chosen base. No effect on the default
  config (5.2 − 0.5 = 4.7, already above the floor); it only bites when the
  adaptive threshold has walked down toward the minimum.

### Tests
- `test_pipelines.py` section 10 — 5 regression tests: per-mode floors for both
  modes, sub-minimum override respected, HTF downgrade surviving the news
  overlay, and a source guard against re-adding the `_threshold` overwrite.
  Verified to fail against the pre-fix sources (4/5; the spot-floor test passes
  in both, guarding the path that was already correct). Suite: 44/44.

---

## 2026-08-29 — fix: Phase 2 and Phase 4 error reporting

### Fixed
- **Phase 2 reported the wrong error.** `analyze_spot_signal()` /
  `analyze_futures_signal()` caught every exception, logged it, and returned
  `None`; `run_bot.py` then did `spot_signal["type"]` on that `None`, so the
  terminal showed `'NoneType' object is not subscriptable` while the real
  cause — a ccxt `NetworkError` from the Binance 403 — only appeared in the
  log. Both pipelines now `logger.exception(...)` (full traceback to
  `spotsignal.log`) and re-raise. `run_bot.py` is the only caller, and it
  already wraps both calls and keeps the cycle alive, so the swallow bought
  nothing. Phase 2 also guards against a `None` return and prefixes the
  exception class, so the message reads
  `SPOT failed (NetworkError: binance GET https://api.binance.com/...)`.
- **Phase 4 announced sends that never happened.** `send_signal_alert()`
  returns early when both signals are `None`, but `run_bot.py` printed
  "✓ Telegram sent" unconditionally — including on a cycle where Phases 2 and
  3 had all failed and nothing was transmitted. `send_signal_alert()` and
  `_send_combined_telegram()` now return the number of messages actually
  delivered (0 when credentials are missing, rather than a bare `None`), and
  Phase 4 reports one of three honest outcomes: `Telegram sent (N messages)`,
  `Skipped — no signal to report`, or `Nothing delivered — check TELEGRAM_* in
  .env / spotsignal.log`. The third case did not exist before: a Telegram
  transport failure (bad token, bad chat_id, HTTP error) also read as "sent".
- **4 regression tests** (`test_pipelines.py` §9) covering the pipeline
  re-raise, the zero-delivery count, an honest delivered count, and the
  no-credentials path. Suite 35 → 39.

### Known gap
- Phase 3 still reports without the exception class
  (`Phase 3 Failed (binance GET https://...)`), the same pattern fixed above
  for Phases 2 and 4.

---

## 2026-08-29 — feat: Binance public-data-mirror fallback

### Added
- **Mirror fallback for spot reads** (`signals/market_data.py`). The bare
  `exchange = ccxt.binance()` had no fallback, and a full cycle lost Phase 2
  *and* Phase 3 when api.binance.com answered 403 at the Cloudflare edge
  (geo-restriction; confirmed a genuine Binance response via `cf-ray`, not a
  local network issue). `_BinanceWithMirror` now retries the spot reads in
  `_MIRRORABLE` (`fetch_ohlcv`, `fetch_ticker`, `fetch_tickers`,
  `fetch_order_book`) against `data-api.binance.vision`, which serves the same
  public spot endpoints unrestricted. Call sites are unchanged — the proxy
  delegates by attribute.
  - `fetchMarkets=['spot']` on the mirror client keeps `load_markets()` off
    fapi.binance.com, which has no mirror and is blocked alongside the primary.
  - Futures-only reads (funding, open interest, L/S ratio, basis) deliberately
    stay on the primary and degrade to NEUTRAL as before — the mirror cannot
    serve them, so routing them there would swap one failure for another.
  - The fallback is sticky so a tripped cycle does not pay the timeout twice,
    and expires after 30 min (`_MIRROR_RETRY_AFTER_S`) so a long `--loop` run
    returns to the primary, and its futures data, once the block lifts.
- **6 tests** (`test_pipelines.py` §8) — mirror URL/spot-only config,
  NetworkError retry, stickiness, cooldown re-probe, futures reads never
  leaking to the mirror, and `__setattr__` reaching both clients. Offline,
  matching the suite's no-network contract. Suite 29 → 35.

---

## 2026-08-29 — docs: merge PAPER_TRADE_PLAN into TODO_PLAN

### Changed
- **`PAPER_TRADE_PLAN.md` merged into `TODO_PLAN.md` and deleted.** The two
  documents described the same paper-trade validation with conflicting
  numbers (2 weeks vs 1 week; WR > 35% futures / > 45% spot vs WR ≥ 50%).
  `TODO_PLAN.md` is the newer, post-audit-#12 document, so its targets win;
  the older bar is recorded inline as a deliberate fallback, not deleted.
  Carried over: weekly analysis queries (frequency, gate-block distribution,
  WR/PF, PnL by F&G zone, daily P&L curve), the red-flag table, and the five
  tuning levers — all five verified still present in `config.py` / `run_bot.py`.
  Dropped as obsolete: the never-filled `<start-date>` period, the decision
  matrix, and "Promote to v2.8.0" — `v2.8.0` (`0f0c2c7`) was tagged
  2026-05-14, the same day as `v2.8.0-rc1` (`e0a7d08`), so the 2-week
  validation it gated on never ran.

### Fixed
- **Wrong rollback target in the retired plan.** It named `cd1f378` as "the
  `v2.7.0` state"; `v2.7.0` is actually `09f0ba6`, and `cd1f378` is merely an
  ancestor of it. The merged rollback section points at the tags instead.
- **`TODO_PLAN.md` drift corrected** — BE-cushion line refs `~236`/`~311` →
  actual `247`/`330` in `trading/paper.py`; Phase 1 now creates `logs/`, which
  never existed and would have failed the `nohup` redirect; the "push develop"
  checklist item marked done (`origin/develop` == `ce85bc1`); adaptive-threshold
  cold start upgraded from possible to certain (both threshold state files are
  gone); new Phase 0 pre-flight covering the current empty-DB state.

---

## 2026-08-29 — chore: remove dead code, trim CLAUDE.md to non-derivable guidance

`/doctor` cleanup pass. Removed files nothing could reach, plus documentation a
session can read straight from the code, and corrected constants that had
drifted out of date.

### Removed
- **`notifier.py` shim deleted** — dead code. A `notifier/` package and a
  `notifier.py` module sat side by side, and Python resolves the package
  first, so the shim could never be imported. Verified: `import notifier`
  resolves to `notifier/__init__.py`, and the suite passes without it.
- **`core_analysis.py` shim deleted** — no module imported it, and it had no
  `__main__` block, so the documented `python3 core_analysis.py` command was
  a silent no-op. Removed the command from `CLAUDE.md` and `README.md`.
- **`test_telegram.py` deleted** — unreferenced one-off credential check.
- **`spotsignal.log`, `.DS_Store`, and all `__pycache__/` removed** (~1.6 MB),
  including 14 orphaned `.pyc` files for modules that no longer exist
  (`signal_engine`, `paper_trader`, `guwek_oracle_v1`, `notifier/discord`, …).

### Fixed
- **Stale max-score constants** — CLAUDE.md documented `SPOT_MAX_SCORE` as
  17.25 and `SIGNAL_MAX_SCORE` as 21.25; `config.py` has 21.75 and 25.75.
  Dropped the hardcoded numbers rather than re-pinning them.
- **Stale condition count** — the 18-row condition table predated conditions
  19 (candlestick patterns), 20 (MFI), 21 (CMF) and 22 (taker ratio) in
  `signals/engine.py`.

### Changed
- **CLAUDE.md 9,580 → 5,572 chars** (~2,395 → ~1,393 est. context tokens).
  Cut the config-variable table (superset lives in `.env.example` and
  `README.md`), the `signals/` module table (each row was that module's own
  docstring), the 18-row condition table, the "Useful SQL queries" block
  (identical copy in `README.md`), and the "Adaptive threshold" section
  (documented in `README.md`, constants in `config.py:111-118`).
  Kept the run commands, phase behaviour contracts, the `mode=` query
  convention, the scoring-convention rules, and the backtest caveats.
- Docstrings in `news_scraper.py` and `trading/history.py` no longer name the
  deleted `core_analysis` shim.

---

## 2026-06-13 — audit #12: entry-quality triple — structural SL, R:R floor, entry-wick gate

User-requested follow-up to minimise losses on opening signals. Three
geometry-and-quality gates added at the entry point — not more filters, but
better mechanics for the signals that do fire.

| Mode    | Sigs (11→12) | WR (11→12)  | PnL (11→12)        | PF (11→12) |
|---------|--------------|-------------|--------------------|------------|
| Futures | 2 → 1        | 100% → 100% | +1.17% → +0.05%    | ∞ → ∞      |
| Spot    | 1 → 1        | 100% → 100% | +3.18% → +3.18%    | ∞ → ∞      |

One STRONG futures winner (+1.13%) was filtered by the entry-wick gate
(82% upper wick at resistance $76,241). It happened to win because the
broader trend pushed through, but an 82% upper wick *is* a real rejection;
filtering it is the correct trade-off for "minimise losses" — we sacrifice
a marginal winner to systematically avoid in-the-moment-rejected entries.

### Changed

- **Structural SL placement** (`signals/engine.py`): replaced the fixed
  `entry ± ATR × 1.5` with `max(swing_low_20 − 0.25×ATR, entry − 2.5×ATR)`
  for BUY (mirror for SELL). The tighter of the two wins — structure if
  close enough, ATR cap otherwise. Stops the SL from sitting at obvious
  liquidity zones that get hunted on normal pullbacks. Reason cited in
  signal: 🔧 SL at swing low … / SL capped at entry − 2.5×ATR.

### Added

- **Entry-candle wick rejection gate** (`signals/engine.py`): if the entry
  candle's own upper wick is > 50% of its range, reject BUY (mirror for
  SELL on lower wick). This is different from the 24-candle fakeout gate
  in `_passes_entry_gates` — that one looks at range extremes; this catches
  single-candle reversals at the entry point itself.

- **Minimum realised R:R gate** (`signals/engine.py`): after every SL/TP/SR
  cap has run, require `(TP − entry) / (entry − SL) ≥ 1.5`. Even 60% WR
  loses money at R:R 0.8; this floor makes the geometry alone profitable
  at break-even WR. Stored as `signal['rr']` for diagnostic visibility.

---

## 2026-06-13 — audit #11: per-loss forensics → anti-FOMO + short-term momentum gates

Ran `scripts/loss_forensics.py` against the two remaining losses in audit #10's
backtest and found two distinct signatures that the engine had no protection
against. Implemented one targeted gate per pattern.

| Mode    | Sigs (10→11) | WR (10→11)   | PnL (10→11)         | PF (10→11)   |
|---------|--------------|--------------|---------------------|--------------|
| Futures | 3 → 2        | 67% → **100%** | −0.04% → **+1.17%**  | 0.97 → **∞** |
| Spot    | 2 → 1        | 50% → **100%** | +1.02% → **+3.18%** | 1.47 → **∞** |

### Added

- **Anti-impulse (FOMO) gate** (`signals/engine.py`): block BUY if the
  *previous* candle's body is ≥ +1.25×ATR up (mirror −1.25×ATR for SELL).
  Forensic: 2026-04-23 15:00 BUY $78,447 entered right after a +1.36×ATR
  green candle — engine scored bullish on the breakout but price reverted
  in 2 candles for −1.21%. The 1.25 threshold caught it; 1.0 was too tight
  (killed winners), 1.5 was too loose (missed this case).

- **Short-term momentum gate** (`signals/engine.py`): block BUY if the
  5-candle SMA slope (over the last 5 candles) is < −0.5×ATR. Mirror +0.5
  for SELL. Forensic: 2026-04-19 12:00 BUY $75,960 entered on the third
  green candle of a 4-day downtrend; engine cited "EMA200 still rising"
  but the 5-SMA had dropped −0.61×ATR. EMA200 sees the long trend; the
  5-SMA gate sees the immediate one. Result: −2.16% loss filtered.

### Caveat — overfit risk

The gates were tuned to the two specific losses in a 90-day sample. With
only 3 surviving trades after the gates, this is **plausibly overfit**.
Recommended next steps before promoting to live:
1. Re-run on a 180+-day dataset spanning a non-bullish regime.
2. Forward-paper-trade for 1–2 weeks with the new gates enabled.
3. If those losses come back, the gates may be too tight — relax the
   thresholds back toward 1.5×ATR / −1.0×ATR.

The thresholds are concentrated in `signals/engine.py` (search "audit #11")
for easy reversion. The Python engineering is sound; the parameter values
need more data to validate.

### Added (tooling)

- `scripts/loss_forensics.py` — for each LOSS in the offline backtest,
  prints the 10 pre-entry candles, post-entry path, indicator state, all
  engine reasons, plus structural diagnostics (close vs EMA200/VWAP in ATR
  units, BB position, recent pullback room). This is how the two patterns
  above were identified; rerun it any time a new loss appears.

---

## 2026-06-13 — audit #10: tuned no-chase to 0.5×ATR + ported BE-cushion to live spot

Two-step follow-up to audit #9.

1. **Empirically tuned the no-chase gate width.** Recommended a loosening to
   1.0×ATR for more volume; the data on `data/btc_*_90d.csv` disagreed:

   | Width    | Futures sigs | PF   | PnL    | Spot PF |
   |----------|--------------|------|--------|---------|
   | 0.5×ATR  | 3            | 0.97 | −0.04% | 1.47    |
   | 0.75×ATR | 4            | 0.76 | −0.37% | 1.47    |
   | 1.0×ATR  | 7            | 0.38 | −1.87% | 0.52    |

   Settled at 0.5×ATR. The marginal entries added by loosening were the
   exact paper-cut setups the gate is designed to catch. Tested values
   documented inline in `signals/engine.py` so the choice is auditable and
   the user can revisit if a wider, two-sided sample disagrees.

2. **Ported the TP1 BE-cushion to live `trading/paper.py` spot path.**
   Per the agreed "PF ≥ 1 before porting" gate, spot cleared 1.47. When a
   spot position partial-closes at TP1, the trail now goes to
   `entry − 0.5×current_atr` instead of exact entry. SELL path mirrored
   for completeness even though spot is BUY-only by design. Futures path
   untouched — futures PF is still 0.97, just under the gate. The
   `partial_close_position(new_sl=…)` call now passes the cushioned SL so
   DB state matches the in-memory trail.

---

## 2026-06-13 — audit #9: timing + confidence — STRONG no longer fires at tops

After audit #8 the remaining leak was signal *timing*: STRONG signals kept
firing at LOCAL TOPS in bull legs (everything aligns most strongly at price
extremes), and the engine had no protection against re-entering the exact
setup that just stopped out.

Results vs audit #8:

| Mode    | Sigs (8→9) | WR (8→9) | PnL (8→9)    | PF (8→9)  |
|---------|------------|----------|--------------|-----------|
| Futures | 33 → 3     | 33% → **67%** | −10.79% → **−0.04%** | 0.34 → **0.97** |
| Spot    | 6 → 2      | 17% → **50%** | −3.98% → **+1.02%**  | 0.44 → **1.47** |

Spot PF cleared 1.0 (eligible to port BE-cushion to `trading/paper.py`).
Futures PF essentially breakeven — a couple of percentage points of trail
tuning would put it above 1.0. Volume is now low (3 / 2 signals) — the next
round is about loosening selectively without re-introducing the paper cuts.

### Added

- **No-chase gate** (`signals/engine.py`): after type set, if entry is more
  than 0.5×ATR away from VWAP_24, force HOLD. The 2026-05-10 BUY at $82,118
  (within $1 of the local high in a leg that ended at $77,188 — STRONG
  confidence, score 9.8) was the canonical case: every condition aligned
  *because* price was extended. Symmetric BUY/SELL.

- **Same-side cooldown after exit** (`backtest.py:run_backtest`): 5 candles
  on 1H, 2 on 4H. Stops the loop from re-entering the same losing setup
  immediately after the trail fires. Mirrors live behaviour somewhat — live
  is naturally cadence-gated by `--loop 60`, but post-exit re-entry was a
  real overcounting source in backtest.

### Changed

- **`get_signal_confidence` now requires HTF agreement for STRONG**
  (`signals/market_data.py`): a score ≥1.5×threshold *plus* `htf['1d']`
  matching the direction. If the score is in STRONG zone but the daily HTF
  disagrees, downgrade to NORMAL. In audit #8 STRONG WR was 12.5% vs NORMAL
  40% (inverted); after this change the only STRONG signal that fired in 90
  days was the +1.13% win on 2026-04-20.

### Fixed

- **Engine session-bump used wall-clock instead of candle time**
  (`signals/engine.py`): `datetime.now(UTC).hour` evaluated every historical
  candle as if it were the hour the backtest was launched (so every bar got
  the same +0.5/−0.25 bump, dictated by when you ran the script). Now reads
  the latest candle's index hour with wall-clock fallback. This was a silent
  consistency bug that made backtest threshold behaviour deterministic-but-
  wrong; live wasn't affected.

---

## 2026-06-13 — audit #8: offline backtest on data/ + 5-pack improvements

Ran the offline pipeline against `data/btc_1h_90d.csv` / `btc_4h_90d.csv`
(BTC +15.5% over the 90-day window). Result before changes:

| Mode    | Sigs | WR    | PnL     | PF   | Avg Hold |
|---------|------|-------|---------|------|----------|
| Futures | 21   | 14.3% | −11.24% | 0.09 | 4 candles |
| Spot    | 6    | 16.7% | −6.79%  | 0.06 | 2 candles |

After P1–P3 + two latent-bug fixes uncovered during validation:

| Mode    | Sigs | WR    | PnL     | PF   | Avg Hold |
|---------|------|-------|---------|------|----------|
| Futures | 33   | 33.3% | −10.79% | 0.34 | 9 candles |
| Spot    | 6    | 16.7% | −3.98%  | 0.44 | 6 candles |

WR doubled (futures), PF improved 4×, avg hold doubled. PnL still negative —
signal *timing* is the remaining issue (BUYs firing at local tops in bull legs).

### Fixed

- **HTF computation in backtest silently dead** (`backtest.py:_compute_htf_from_df`):
  the helper called `.set_index("timestamp")` on a DataFrame whose timestamp
  was *already* the index, so it raised KeyError on every call. The outer
  `try/except` swallowed the exception, set `htf=None`, and Condition 6
  (HTF alignment, 2.0 max points) never contributed anything in 90 days of
  backtest. Made every backtest look quieter than live for months. Removed
  the redundant `set_index`.

- **Backtest fired same-direction signals while a position was still open**
  (`backtest.py:run_backtest`): live caps at 1 BUY + 1 SELL via
  `max_positions`, but the backtest loop treated each candle independently.
  Once the HTF bug was fixed and BUY signals started firing at every hourly
  alignment, the loop generated 65 BUY trades in 90 days — none of which
  would have been entered live. Added `open_until` per-direction state so a
  new signal is skipped if a same-side trade is still active.

### Changed (P1)

- **Trail ATR factor loosened**: futures `0.9 → 1.5`, spot `1.0 → 2.0`
  (`config.py:RISK_CONFIG`, `FUTURES_CONFIG`). Backtest showed multiple
  directionally-correct SELLs (e.g. 2026-05-10 short at $80,662 in a leg
  that closed at $77,188) trailed out in 2 candles for tiny losses. The old
  multipliers were below typical BTC noise.

### Changed (P2)

- **TP1 no longer snaps trail to entry** (`backtest.py:_simulate_forward`):
  after partial close, trail is now pulled to `entry − 0.5×ATR_now` (BUY)
  or `entry + 0.5×ATR_now` (SELL) instead of entry exactly. At true BE, exit
  fees made every remainder a fee-tax LOSS even when TP1 hit. Half-ATR
  cushion preserves the locked partial while letting normal noise breathe.
  Live path in `trading/paper.py` still snaps to entry — pending PF ≥ 1.0
  before porting per the agreed gating.

### Added (P3)

- **Hard counter-trend block** (`signals/engine.py:generate_signals`):
  if `htf['1d']` is BULLISH and the signal is SELL → force HOLD. Mirror for
  BEARISH + BUY. Only fires when 1D HTF is non-NEUTRAL (i.e. we actually
  have higher-TF context). Backtest had 12 counter-trend SELLs in a +15.5%
  uptrend with 8.3% WR; new block drops that to 5 SELLs with the worst
  trend-fighters filtered out.

---

## 2026-05-14 — fix: 4 audit pass — macro race, trail ATR, csv silent, signal outcome semantics

### Fixed

- **MACRO open-then-immediately-close in same cycle** (`signals/engine.py:integrate_news_with_signal`): The old code reduced strength by 2.0 on macro detection — if strength still cleared threshold, the signal fired → position opened in Phase 3 step 2 → `check_and_close_positions` at step 4 detected the same macro and MACRO_CLOSE'd it. Entry fee + exit fee + slippage burned for a zero-duration trade every macro window. Now forces type='HOLD' on macro detection regardless of strength.

- **Live trail used ENTRY ATR but backtest used CURRENT ATR** (`trading/paper.py`): In expanding-vol regimes the live trail stayed at the (smaller) entry-ATR width while backtest correctly widened with current vol — live got whipsawed out of trades that backtest holds onto, so backtest overstated live performance. Live now uses `current_atr` from `check_and_close_positions` for the trail-width calculation, falling back to entry ATR when unavailable. Symmetric BUY/SELL.

- **`check_upcoming_macro_events` silent fallback** (`signals/sentiment.py`): A blanket try/except returned `(False, None)` on any error. A missing or corrupt MACRO_CSV silently disabled macro protection — bot would trade through high-impact news with no force-close. Now logs WARNING on CSV-level failures so the operator can see protection is offline; per-row parse errors still skip at DEBUG.

- **`signals.outcome` polluted with cut-short close labels** (`trading/history.py`): `close_paper_position` propagated every outcome label (VOL_EXIT, TIME_EXIT, FUNDING_EXIT, MACRO_CLOSE, FLIP, BREAKER_CLOSE) to the linked signals row. These don't reflect signal quality — they're "we force-closed for unrelated reasons". A retrospective query mixing them with WIN/LOSS drags the analysis. Now only WIN/LOSS propagates; cut-short closes leave `signals.outcome` NULL (unresolved). `paper_positions` still records the full outcome detail.

---

## 2026-05-14 — fix: backtest alignment + pyramid TP min-distance

### Fixed

- **Backtest VOL_EXIT diverged from live** (`backtest.py:238`): live just changed to only fire VOL_EXIT when underwater; backtest still closed any position on vol expansion. Backtest would underreport win rate vs what live now achieves. Now mirrors the underwater-only gate.

- **Backtest trade timestamps always empty** (`backtest.py`): `fetch_ohlcv_df` sets `timestamp` as the DataFrame INDEX, but `_make_trade` and `_candle_utc_hour` accessed it as a COLUMN (`df.iloc[i]["timestamp"]` → KeyError → silently empty). Trades had blank entry_time/exit_time, and the US-session bump in classify_regime always saw hour=0. Now reads `df.index[i]`.

- **Arbitrary 12% "funding exit proxy" in backtest** (`backtest.py`): With no historical funding data, the backtest invented a 12% unrealised-PnL cap and called the exit FUNDING_EXIT. This wasn't modelling funding — it was just capping winners, biasing avg P&L down and creating a phantom exit type. Removed cleanly; module docstring already disclaims that market-structure exits don't apply in backtest.

- **Backtest entry gates asymmetric** (`backtest.py:_passes_entry_gates`): only checked fakeout on BUY upper-wick (not SELL lower-wick); quality gates (regime, confluence, breakout, psy_sl) applied to spot BUY only — futures SHORT skipped all of them. After the recent live changes that added counter-trend regime + confluence + psy_sl + sr to futures first-entry, backtest SHORT would over-report performance vs live. Now mirrors live: symmetric BUY/SELL fakeout, symmetric regime/confluence/psy_sl for both spot and futures.

- **Pyramid TP cap could land just above entry** (`run_bot.py:618`): the recent S/R cap fix on the pyramid TP recompute lacked the engine's "min 1× ATR distance" check. If resistance was very close to entry (e.g. entry 80000, resistance 80050), TP1 would be capped to 80050 — pyramid would TP1 for negligible profit then trail-to-BE the other half, netting essentially zero. Now requires `ceiling - entry >= 1× ATR` before capping.

---

## 2026-05-14 — fix: 4 strategy bugs that bias toward losing money

### Fixed

- **Trail/SL exit P&L computed off trigger, fill reported as current** (`trading/paper.py`): When price gaps through the trail or original SL, the actual fill price is `current_price` (worse than the trigger). The old code computed `exit_pnl = _calc_pnl(pos, trail)` but reported `exit = current_price` in the close record — paper P&L systematically overstated wins (or understated losses) by the slippage gap on every trail/SL exit. False confidence in strategy performance. Now uses `current_price` for both the P&L and the recorded exit; `_check_slippage` warning still logs the trail-to-fill discrepancy.

- **F&G double/triple-counted in news integration** (`signals/engine.py:integrate_news_with_signal`): `news_data['sentiment']` is computed as `combined = fng_score*0.50 + crypto_score*0.35 + geo_score*0.15` (see `sentiment.py:69`), so Fear & Greed is already weighted 50% INTO the news sentiment. The old code then applied a SECOND independent bump on F&G value (±1.5 / ±0.5). A BUY at F&G=15 with BULLISH sentiment got +1.5 (F&G direct) + 0.75 (sentiment) = +2.25, but 50% of that 0.75 was F&G being re-counted. Inflated scores promoted weak signals past the NORMAL confidence floor and into STRONG tier, bypassing entry gates. Now: F&G has its own tier check; news-sentiment only fires when F&G didn't trigger; news-sentiment weights reduced (0.75→0.5, 0.5→0.35) to reflect the remaining non-F&G signal.

- **VOL_EXIT force-closed profitable positions** (`trading/paper.py`): When current ATR exceeded `entry_atr × vol_mult`, ALL open positions in that mode were closed regardless of P&L. A trade at +5% in expanding volatility got force-closed — throwing away winners. Now VOL_EXIT only fires when unrealised P&L is ≤ 0. Profitable positions in expanding vol keep running; the trailing stop logic naturally widens to current ATR on every cycle, so risk is still capped.

- **Quadruple-dampened position size in high vol** (`signals/sizing.py:calculate_futures_position`): `vol_cap` multiplied BOTH `effective_risk = risk_pct × conf_mult × vol_cap` AND `effective_max = max_leverage × vol_cap`. Combined with `_compute_confidence` separately deducting 0.15 from `mult` when `atr_pct > 0.85`, position size collapsed to ~10% of base in extreme vol — missing the high-vol periods that often contain the best setups. Now `vol_cap` applies only to the leverage ceiling; risk-side dampening lives entirely in `_compute_confidence`. Position value across regimes smooths from $700 (low vol) → $300 (extreme vol) instead of collapsing.

---

## 2026-05-14 — fix: 7 audit pass (terminal pd scope, telegram SL sign, double-icon, cycle resilience, OHLCV validation, import cleanup)

### Fixed

- **`_pd` import scope in MFI/CMF display** (`signals/terminal.py`): `import pandas as _pd` was inside the `if mfi is not None:` branch, so a signal with `mfi=None` but `cmf` present would `NameError` when computing `_pd.isna(cmf)`. In practice MFI and CMF are computed together so it never tripped live, but the coupling was wrong. Hoisted import above both branches.

- **SL displayed as `+5.00%` in compact Telegram** (`notifier/telegram.py`): `_format_compact_signal_telegram` used `{sl_pct:+.2f}%` where `sl_pct` is `abs(entry - sl)/entry * 100` (always positive). The `:+.2f` rendered "+5.00%" for a stop loss — implies a gain. Consolidated and open-notification formatters already used `-{sl_pct:.2f}%` correctly; compact card now aligned.

- **Phase 3 actions printed with double-icon** (`run_bot.py`): The summary loop unconditionally prepended "✓" or "⏭" to every action, but breaker/emergency entries already carry their own icon ("⛔" / "🚨"), producing "⏭ ⛔ SPOT drawdown ...". Now detects self-iconed entries and prints them as-is in a colour matching the icon family.

- **Phase 4 + main loop unguarded against exceptions** (`run_bot.py`): A malformed signal that crashed a Telegram formatter would propagate out of `run_cycle()` and stop the whole `while True` loop. Both the Phase 4 send block and the loop's cycle dispatch now wrap in try/except (KeyboardInterrupt still propagates so Ctrl+C works). One bad cycle no longer kills the bot.

- **`_validate_ohlcv` was dead code** (`signals/ohlcv.py`): Function defined but never invoked. Empty bars or NaN in close/high/low/volume would silently flow into indicator calcs and produce nonsense EMA/RSI/MACD the engine would happily score from. `fetch_ohlcv_df` now calls the validator and raises `ValueError` on failure; the per-mode orchestrators already catch exceptions → bad fetch becomes HOLD rather than corrupt signal.

### Changed

- **notifier/telegram.py imports sizing directly** instead of through the `core_analysis` legacy shim. Removes coupling to the shim's continued existence.

---

## 2026-05-14 — fix: 5 post-audit issues (flip-path gates, display order, pyramid TP cap, pyramid size order, loop scheduling)

### Fixed

- **Flip path skipped 4 of the new quality gates** (`run_bot.py`): The futures FIRST-entry path got psy_sl + sr_entry + counter-trend regime + trend-confluence gates in the previous commit, but the FLIP path (signal reversed, opposite position auto-closed, new direction opened) ran only profitability + confidence + fakeout — bypassing the regime/structure protection. A SHORT flipping a LONG in TRENDING_BULLISH would open anyway. All four gates now mirror to the flip path with `flip_*` tags so `signal_blocks` distinguishes them.

- **VERDICT box rendered before circuit breaker mutated signal type** (`run_bot.py`): Terminal showed "STRONG BUY" while Telegram (sent later) showed HOLD because `display_combined()` ran before the per-mode drawdown breaker. Display moved to after the breaker so VERDICT reflects the blocked state.

- **Pyramid TP recompute ignored engine's resistance cap** (`run_bot.py`): When SL was tightened for pyramid entries, TP1 and TP2 were rebuilt from `entry + new_sl_dist × RR`, discarding the resistance cap the engine had applied to the original TP. A pyramid opened near resistance could land TP past it. Now caps recomputed TPs at `resistance × 0.995` when resistance sits between entry and the raw TP.

- **Pyramid sized against wide SL then opened with tight SL** (`run_bot.py`): `calculate_position_size()` was called BEFORE SL tightening. Position size scales inversely with SL distance, so sizing for a wider SL gave a smaller position than warranted by the tighter SL — capital-at-risk per pyramid entry came in below the configured 2%. Currently masked by max_position_size cap at 10%, but the order was logically wrong. Reordered: tighten SL → compute size → check min_size → check aggregate risk → open.

- **`--loop ≥ 120` collapsed mid-cycle check onto full-cycle minute** (`run_bot.py`): `half = args.loop // 2` then `check_minute = (full_minute + half) % 60` — for `loop=120` this gave `(1 + 60) % 60 = 1`, same as `full_minute`. Mid-cycle position check never ran for long intervals. Now sets `check_minute = None` (and skips the mid-cycle leg cleanly) when `half ≥ 60` or when the wrap collides.

---

## 2026-05-14 — feat: 4 entry-strategy improvements (psy_sl SHORT, regime/confluence for futures, reentry tier)

### Fixed

- **`_check_psychology_sl_risk` was BUY-only logic** (`run_bot.py`): The function checked "distance from SL up to the next round number ABOVE it" — correct for BUY (SL below entry, vulnerable to a push down through the round). For SHORT, SL is above entry and the relevant magnet is the round number BELOW the SL (push up through it triggers the stop). Now accepts `direction=` and uses the right side: `dist = sl_above - sl` for BUY, `dist = sl - sl_below` for SELL.

### Added

- **Counter-trend regime block, symmetric BUY/SELL** (`run_bot.py`): New `_is_counter_trend_regime(signal, direction)` blocks BUY in TRENDING/VOLATILE BEARISH AND SELL in TRENDING/VOLATILE BULLISH. Spot already had `_is_bearish_regime` for BUY; futures had **no equivalent for SHORT**, which meant a SELL hitting NORMAL confidence (≥1.2× threshold) could open into a strong bull. Now applied to futures first entry; spot continues to use the back-compat wrapper.

- **Trend confluence for both directions** (`run_bot.py`): `_trend_confluence_for_direction(signal, direction)` requires ≥2/3 confirmations (price vs EMA200, ADX trend_dir, price vs VWAP) MATCHING the signal direction. Applied to futures first entry as a `trend_confluence` gate — previously only spot had a bullish confluence check; now SHORT also needs ≥2/3 bearish confirmations.

- **psy_sl + sr_entry gates added to futures first entry** (`run_bot.py`): These existed only on the spot path. Now mirrored for futures, with `direction=` passed through correctly so SHORT SL checks use the round-number-below logic.

### Changed

- **Re-entry quality: confidence tier OR strength +0.3** (`run_bot.py:_check_reentry_quality`): Was `new_strength >= last_strength + 0.5`. With threshold 5.0 and typical scores 5.0–6.5, +0.5 was effectively unreachable without an explicit STRONG-tier jump. Now allows re-entry on any of (1) better price, (2) confidence tier upgrade (WEAK → NORMAL → STRONG), or (3) raw strength +0.3 fallback. Uses `get_signal_confidence(strength, threshold)` to compute the tier of the historical signal — current threshold is used as proxy (adaptive drifts slowly enough).

---

## 2026-05-14 — feat: 5 strategy improvements (sample size, signal outcome, per-mode breaker, reentry filter, gate tracking)

### Changed

- **Win-rate min sample raised 3 → 5** (`signals/market_data.py`): 3 trades = ±33% standard error; far too noisy to base ±0.5–1.0 threshold moves on. Adaptive threshold now waits until 5 resolved WIN/LOSS trades are in the window before reacting. (Wilson lower-bound smoothing was prototyped and rejected — too conservative for the "lower threshold" path at small n.)

- **`signals.outcome` now updated when paper position closes** (`trading/history.py`): Previously dead code. `close_paper_position()` now propagates the outcome and timestamp to the linked `signals` row via its `signal_id` FK. Enables retrospective queries like "score 6.5 STRONG → WIN/LOSS distribution" that were impossible before because every `signals.outcome` was an empty string.

- **Drawdown circuit breaker is per-mode** (`run_bot.py`): Previously combined spot+futures into `total_dd`. A 12% futures collapse was masked by a 5% spot gain. Spot and futures now block independently — only the offending mode pauses new entries. Daily-loss check is also per-mode (`get_daily_pnl("spot")` and `get_daily_pnl("futures")` separately). Telegram circuit-breaker card shows both modes' DD and daily P&L for transparency.

- **Re-entry quality benchmark = last RESOLVED WIN/LOSS in same direction** (`run_bot.py:_check_reentry_quality`): Previously compared against the most recent close of any kind in the mode — a quick FLIP at +0.1% or a VOL_EXIT at −0.3% would set an arbitrary baseline for the next BUY entry. Now filtered to `outcome IN ('WIN','LOSS') AND type = ?` so the benchmark is a meaningful executed trade in the same direction.

### Added

- **`signal_blocks` table for gate-block tracking** (`trading/history.py`, `run_bot.py`): New table records every Phase 3 gate rejection — mode, signal type, gate tag (e.g. `confidence_first`, `fakeout_pyramid`, `regime_bearish`), reason, strength, confidence, and FK to the signal. 29 block sites in `run_bot.py` instrumented via a single `_block(phase3_actions, mode, signal, gate, reason)` helper that writes to DB, logger, and phase3 status in one call. Lets us answer "which gate blocks the most signals" so the strictest gates can be tuned.

  Suggested SQL:
  ```sql
  SELECT gate, COUNT(*) FROM signal_blocks
   WHERE timestamp >= date('now','-7 days')
   GROUP BY gate ORDER BY 2 DESC;
  ```

---

## 2026-05-14 — feat: complete outcome labels in Telegram + terminal display

### Changed

- **Telegram close notification labels** (`notifier/telegram.py`): Added human-readable labels for the four exit outcomes that previously fell through to raw `outcome` text: `TIME_EXIT` → "max hold time", `VOL_EXIT` → "volatility expansion", `FUNDING_EXIT` → "funding cost exit", `BREAKER_CLOSE` → "circuit breaker · equity".
- **Terminal performance breakdown** (`trading/paper.py`): `print_open_status()` and `print_paper_summary()` now report counts for VOL/TIME/FUNDING exits and BREAKER_CLOSE in addition to W/L/MC/BE. Short labels in the open-status one-liner (`VX`/`TX`/`FX`/`BR`), long labels in the paper summary block (`N Vol`/`N Time`/`N Fund`/`N Breaker`).
- **Telegram consolidated performance section** (`notifier/telegram.py`): Same outcome breakdown for spot/futures performance line — no more silent drops.

---

## 2026-05-14 — fix: closed_at corruption + recent win-rate denominator

### Fixed

- **`closed_at` column corrupted with price strings** (`trading/history.py`, `trading/paper.py`, `run_bot.py`): Five call sites (`TIME_EXIT`, `VOL_EXIT`, two `FUNDING_EXIT`, `FLIP`, plus the newly added `BREAKER_CLOSE`) were passing the exit *price* as the `closed_at` argument to `close_paper_position()`. Because SQLite stores TEXT columns as-is, rows ended up with `closed_at = "80938.53"` instead of an ISO timestamp. This silently broke two queries that compare `closed_at` lexicographically:
  1. `get_daily_pnl()` — daily-loss circuit breaker filter `closed_at >= "2026-05-14 00:00:00"`. Any numeric string starting with a digit `>= '2'` lexicographically wins, so historical VOL/TIME exits where BTC traded in the $30k–$99k range were always counted as "today's" P&L. The circuit breaker could fire incorrectly or fail to fire when it should.
  2. `_get_recent_win_rate()` — adaptive threshold cutoff `closed_at >= cutoff_iso`. Same lexicographic trap → over-counted stale closes in the recent window, dragging win rate down and pushing the threshold up.

  Fixes:
  - Added `exit_price REAL` column to `paper_positions`.
  - `close_paper_position()` now accepts `exit_price=` explicitly; numeric `closed_at` is auto-routed into `exit_price` and `closed_at` defaults to the ISO timestamp.
  - Migration repairs historical rows: any `closed_at` that parses as a float is moved into `exit_price` and `closed_at` is set to NULL.
  - All five call sites updated to pass `exit_price=` instead of `closed_at=`.

- **`_get_recent_win_rate()` over-counted non-WIN/LOSS outcomes** (`signals/market_data.py`): The denominator included VOL_EXIT, TIME_EXIT, FUNDING_EXIT, MACRO_CLOSE, BREAKER_CLOSE, FLIP — so a flurry of VOL_EXITs at small losses (caused by the prior ATR-scale bug) dragged the win rate down and triggered aggressive +1.0 threshold raises in the 24h fast-window logic. Filter changed to `outcome IN ('WIN','LOSS')` matching `get_win_rate()`.

---

## 2026-05-14 — fix: per-mode ATR + emergency-close P&L + trail outcome labels

### Fixed

- **Wrong ATR scale passed to futures positions** (`run_bot.py`, `trading/paper.py`): `check_and_close_positions()` for `mode='futures'` was being called with `current_atr` taken from the SPOT signal (4H scale) whenever a spot signal existed. Live data shows spot 4H ATR (~700) is roughly 2× futures 1H ATR (~330), so the VOL_EXIT check `current_atr > entry_atr × 2.0` triggered on essentially every cycle for futures positions opened at 1H ATR. This was the root cause behind every futures position closing via VOL_EXIT in the May 9–13 live run — raising `vol_expansion_exit_mult` to 2.0 helped but did not address the underlying mismatch. Fixed by computing `spot_atr` and `fut_atr` separately and passing each mode its own ATR. Mid-cycle position check now caches per-mode (`_last_atr_spot`, `_last_atr_fut`).

- **EMERGENCY close used `pnl=0`** (`run_bot.py:351-365`): When equity drops below `min_equity_pct` (50%), `close_paper_position()` was called with hard-coded `pnl=0`, masking the actual realised loss in the breaker. Fixed to compute P&L at the latest signal price (futures entry preferred, then spot) with direction-aware sign, and pass `closed_at` for accurate exit records.

- **Trail outcome mislabelled "WIN" when net P&L negative** (`trading/paper.py`): When a trailing stop hit exactly at breakeven (trail = entry) with no partial taken, outcome was labelled `WIN` because the check was `trail >= entry`, but the net P&L after entry+exit fees (~0.18%) and slippage was actually negative. Outcome now labels by `exit_pnl > 0` (post-fee P&L) for both BUY and SELL exits.

- **Candlestick pattern `KeyError` if dict malformed** (`signals/engine.py:530-537`): Direct `cs['bullish']` access would crash if `detect_candlestick_pattern()` ever returned a dict missing those keys. Defensive `.get()` used instead.

---

## 2026-05-14 — fix: VOL_EXIT too aggressive + HTF conflict penalty

### Fixed

- **VOL_EXIT multiplier too low** (`config.py`): `vol_expansion_exit_mult` raised 1.5 → 2.0. At 1.5×, futures positions were closed within 1–2 cycles whenever volatility expanded slightly (ATR 1.5× entry ATR), preventing trades from reaching TP1. All 3 futures positions in live data were VOL_EXIT at small losses. At 2.0× the threshold is more realistic — requires a true volatility spike (doubled ATR) before force-closing.

- **HTF conflict no penalty** (`signals/engine.py`): When both HTF timeframes actively oppose each other (4H BULLISH vs 1D BEARISH or vice versa), the engine previously showed a "caution" note but did not reduce the signal score. In live data, 100% of signals fired with HTF misalignment. Fixed: when `aligned=False` and the two timeframes are directly opposing (BULLISH vs BEARISH — not just NEUTRAL), the dominant side (buy or sell) is penalised −1.0. This makes borderline signals drop below threshold rather than firing against the longer-term trend.

---

## 2026-05-12 — fix: 2 paper-trading position bugs (gap-through P&L, return type)

### Fixed

- **Gap-through TP1+TP2 same-cycle P&L understatement** (`trading/paper.py`): When price gaps through both TP1 and TP2 in a single bot cycle, `pos.get("partial_pnl")` was returning the stale value from the loop-start snapshot (0 for fresh positions) because the `pos` dict was never updated after `sh.partial_close_position()`. Combined P&L was calculated as `0 × 0.5 + remaining_pnl × 0.5` instead of the correct blend. Fixed by adding `pos['partial_pnl'] = pnl` immediately after `sh.partial_close_position()` in both BUY (line 223) and SELL (line 290) TP1 blocks.
- **`check_and_close_positions()` returns `None` instead of `[]`** (`trading/paper.py:117`): When no open positions exist, the function returned `None` implicitly. Callers used `or []` defensively so no crash occurred, but the implicit `None` was a latent bug. Fixed to `return []`.

---

## 2026-05-12 — fix: misleading "pyramid eligible" label in VERDICT display

### Fixed

- **"pyramid eligible" shown when no positions exist** (`signals/terminal.py`): The label `🧩 pyramid eligible` appeared in the VERDICT box for every SPOT STRONG signal, regardless of whether any positions were open. This was confusing because it implies pyramiding happened or should have happened, even when the first-entry gates (fakeout, trend confluence, S/R proximity, etc.) blocked the entry. Fixed: now checks `get_open_position_count_by_direction()` — if open positions exist, shows `🧩 pyramid eligible`; if none, shows `⭐ entry grade: STRONG` instead.

---

## 2026-05-12 — feat: 3 new analysis conditions (MFI, CMF, Taker Ratio) + MACD ADX gate

### Added

- **Condition 20 — MFI (Money Flow Index)** (`signals/engine.py`, `signals/indicators.py`, `signals/ohlcv.py`): Volume-weighted RSI combining price direction + volume to detect institutional flow. Oversold ≤20 → +1.5, buy zone 21–40 → +0.75, sell zone 60–79 → +0.75, overbought ≥80 → +1.5. Applies to both spot and futures.
- **Condition 21 — CMF (Chaikin Money Flow)** (`signals/engine.py`, `signals/indicators.py`, `signals/ohlcv.py`): 20-period accumulation/distribution oscillator measuring institutional buying vs. selling pressure. Strong accumulation ≥+0.25 → +1.0, mild ≥+0.10 → +0.5, mild distribution ≤−0.10 → +0.5, strong ≤−0.25 → +1.0. Applies to both spot and futures.
- **Condition 22 — Taker Buy/Sell Ratio** (`signals/engine.py`, `signals/market_data.py`, `signals/futures.py`): Binance futures aggressive order-flow metric. Ratio ≥1.2 (buyers dominant) → +1.0, ≤0.8 (sellers dominant) → +1.0 against. Futures-only condition.
- **`compute_mfi()`** (`signals/indicators.py`): Money Flow Index calculation (14-period default).
- **`compute_cmf()`** (`signals/indicators.py`): Chaikin Money Flow calculation (20-period default).
- **`fetch_taker_buy_sell_ratio()`** (`signals/market_data.py`): Fetches Binance `/futures/data/takerlongshortRatio` and returns `{ratio, bias}` dict.
- **MFI/CMF in terminal display** (`signals/terminal.py`): Both indicators shown in TECHNICALS section with color-coded oversold/overbought/accumulation tags.
- **MFI/CMF in `_last` dict** (`signals/spot.py`, `signals/futures.py`): Added `mfi` and `cmf` keys for downstream display.

### Improved

- **MACD ADX gate** (`signals/engine.py`): MACD crossover now awards full 1.5 weight only when ADX ≥ 20 (trending market); reduced to 0.75 in ranging markets (ADX < 20) to suppress false signals. Reason appended: "(ADX N — reduced)".
- **ADX pre-computation** (`signals/engine.py`): ADX computed once at the start of `generate_signals()` into `_adx_pre` so condition 3 (MACD) can use it before condition 18 (ADX) executes.

### Config

- **`SIGNAL_MAX_SCORE`** (`config.py`): Updated 22.25 → 25.75 (+1.5 MFI +1.0 CMF +1.0 taker ratio).
- **`SPOT_MAX_SCORE`** (`config.py`): Updated 19.25 → 21.75 (+1.5 MFI +1.0 CMF).

---

## 2026-05-12 — fix: Telegram SELL signal missing confidence label and action guidance

### Fixed

- **SELL header missing confidence tier** (`notifier/telegram.py`): `_format_consolidated_telegram()` SELL branch did not include the confidence label (STRONG/WEAK/NORMAL) in the header line, unlike BUY. Fixed: `conf_str` now appended for SELL too.
- **SELL action guidance not confidence-aware** (`notifier/telegram.py`): BUY has 3 action variants (STRONG/WEAK/NORMAL), SELL always showed the same generic message. Fixed: SELL now shows "strong signal · full size", "weak signal · reduce size", or "signal confirmed" based on confidence tier.

---

## 2026-05-12 — fix: terminal display bugs for FUTURES SHORT

### Fixed

- **VERDICT emoji always 🟢** (`signals/terminal.py`): `display_analysis()` VERDICT section used a hardcoded `🟢` for all active signals including SELL. Fixed: now `🔴` for SELL, `🟢` for BUY.
- **SL percentage missing minus sign** (`signals/terminal.py`): `display_combined()` showed SL as `(2.00%)` without the leading `-`. Fixed to `(-2.00%)` for consistency with `display_analysis()`.

---

## 2026-05-12 — fix: 5 strategy bugs (HTF alignment, RSI bias, cache, liquidation, sizing)

### Fixed

- **HTF NEUTRAL-NEUTRAL falsely aligned** (`signals/htf.py`): When both timeframes returned NEUTRAL, `trend_match = True` because NEUTRAL == NEUTRAL, incorrectly awarding +2.0 HTF alignment score in flat markets. Fixed: alignment requires at least one non-NEUTRAL trend (`htf['4h'] != 'NEUTRAL'`). Same fix applied to spot HTF (`1d != NEUTRAL` before comparing `1d == 1w`).
- **RSI asymmetric scoring** (`signals/engine.py`): BUY zone (RSI 30–50) scored +1.0 while SELL elevated (RSI 55–70) only scored +0.5 — a systematic long bias. Fixed: sell zone broadened to 50–70 and raised to +1.0, matching buy zone weight symmetrically.
- **Spot cache shallow copy** (`signals/spot.py`): Cache hit returned `dict(signal)` which only copied top-level keys; nested dicts (`_market`, `_htf`, `_news_data`) were shared references. Mutation by caller between cache hits could corrupt the cached value. Fixed: `copy.deepcopy()` ensures full isolation.
- **Liquidation price formula** (`signals/sizing.py`): Previous formula `entry * (1 - (1 - mm_rate) / leverage)` gave near-zero values at leverage=1. Fixed: Binance isolated-margin formula `entry * (1 - 1/leverage) / (1 - mm_rate)` with explicit 0/inf guard when leverage=1.
- **Division by zero in leverage calculation** (`signals/sizing.py`): `int(needed_position / max_margin)` could divide by zero if `balance = 0`. Fixed: falls back to `effective_max` when `max_margin <= 0`.

---

## 2026-05-12 — fix: TP2 < TP1 bug + "CrySignal" header in run_bot.py

### Fixed

- **TP2 below TP1 for BUY (and above TP1 for SELL)** (`signals/engine.py`): The resistance/support cap on TP2 did not check whether the capped value would still be beyond TP1. When resistance fell between entry and TP1 (e.g., resistance=$82,479, TP1=$84,220), TP2 was capped at resistance×0.995=$82,067 — less than TP1. Fixed: cap is only applied if the capped value still exceeds TP1 (BUY) or is still below TP1 (SELL). 4 new TP2 unit tests added.
- **"CrySignal" header** (`run_bot.py`): The `run_cycle()` status header displayed "CrySignal · BTC/USDT" at the start of each cycle. Fixed to "SpotSignal · BTC/USDT".

---

## 2026-05-12 — test: pipeline dummy-data test suite (25 cases, all pass)

### Added

- **`test_pipelines.py`**: 25 dummy-data tests covering all pipeline combinations without network or DB. Tests `_mode_label()`, compact formatter, consolidated formatter, terminal `display_combined()`, absence of `_conflict` in codebase, and edge cases (SHORT SL direction, HOLD gap display, None signal handling).

### Fixed

- **VERDICT section label in consolidated Telegram** (`notifier/telegram.py`): The VERDICT section used `"FUTURES"` regardless of direction. Updated to `"FUTURES LONG"` / `"FUTURES SHORT"` / `"FUTURES"` for consistency with the rest of the message.

---

## 2026-05-11 — feat: remove conflict detection — spot/futures/long/short pipelines fully independent

### Changed

- **Conflict detection removed** (`run_bot.py`): Deleted the cross-pipeline suppression block that forced both signals to HOLD when SPOT 4H and FUTURES 1H disagreed in direction. SPOT 4H and FUTURES 1H are different timeframes by design — a 1H bearish signal during a 4H bullish trend is a normal pullback, not a contradiction. Each pipeline now fires independently.
- **Pipeline labels direction-aware** (`notifier/common.py`, `signals/terminal.py`): Futures signals now show as "FUTURES LONG 1H" or "FUTURES SHORT 1H" (instead of generic "FUTURES 1H") making the three independent pipelines explicit: SPOT 4H · FUTURES LONG 1H · FUTURES SHORT 1H.
- **`_conflict` display code removed** (`signals/terminal.py`, `notifier/telegram.py`): All `_conflict` key checks and CONFLICT display branches cleaned up from `_signal_box()`, `_combined_box()`, `display_combined()`, `_format_compact_signal_telegram()`, and `_format_consolidated_telegram()`.

---

## 2026-05-11 — fix: terminal/Telegram display — conflict label, wrong app name, hardcoded count

### Fixed

- **"CrySignal" wrong app name** (`signals/terminal.py`, `notifier/telegram.py`): `display_combined()` header and Telegram consolidated message header showed "CrySignal · BTC/USDT" instead of "SpotSignal · BTC/USDT". Fixed in both places.
- **`_signal_box()` conflict HOLD shows wrong reason** (`signals/terminal.py`): When both pipelines conflict (BUY vs SELL), the HOLD verdict in the single-pipeline display showed "news downgrade → HOLD" (gap < 0 branch) instead of "⚠ CONFLICT". Fixed: `_conflict` key checked first in `_signal_box()`, `_combined_box()`, and the per-mode HOLD section of `display_combined()`.
- **`_format_compact_signal_telegram()` conflict HOLD** (`notifier/telegram.py`): The compact card used as fallback (when only one pipeline runs) showed a negative gap and no conflict context. Fixed: HOLD header now shows "⚠️ CONFLICT" and score line shows "opposing signals cancel".
- **Hardcoded "17" in SIGNAL REASONS label** (`signals/terminal.py`): Changed "of 17" to "active" since condition count has grown past 17.

---

## 2026-05-11 — fix: divergence pivots used close price, cache writes non-atomic, bearish regime incomplete

### Fixed

- **RSI divergence using close instead of high/low** (`signals/indicators.py`): `detect_rsi_divergence` stored `closes[i]` when building swing pivot lists but was comparing them against actual `lows[i]`/`highs[i]` for the lower-low / higher-high check. A hammer candle (close near top, wick near bottom) would fail the bearish lower-low test even though the actual low was lower. Fixed: pivots now store `lows[i]` for swing lows and `highs[i]` for swing highs.
- **Cache writes non-atomic** (`signals/market_data.py`): OI, stablecoin, and BTC dominance cache files were written via raw `open()` instead of the shared `save_cache()` helper, risking corrupt JSON if the process was killed mid-write. Fixed: all three use `save_cache()` which writes to `.tmp` then `os.replace()`.
- **OHLCV staleness check always bypassed** (`signals/ohlcv.py`): `_validate_ohlcv` checked `df.index[-1].timestamp()` to detect stale data, but the DataFrame had an integer index (timestamp was a column), so `.timestamp()` raised `AttributeError` and `last_ts` was always 0. Fixed: `df.set_index('timestamp')` now called before indicators are computed, converting the index to `DatetimeIndex`.
- **`_is_bearish_regime` missed VOLATILE bearish** (`run_bot.py`): The function guarding spot BUY entries only blocked in `TRENDING` + `BEARISH` regimes. A `VOLATILE` + `BEARISH` regime (extreme ATR percentile, DI− dominant) would pass through and allow a spot BUY entry against a confirmed bearish trend. Fixed: added `"VOLATILE"` to the allowed blocking regimes.

---

## 2026-05-11 — fix: strategy review — fees, threshold floor, sizing, config

### Fixed

- **TP1/TP2 exits missing fees** (`trading/paper.py`): TP1 and TP2 exits were computing raw P&L without deducting fees or slippage. Paper trading WIN results were overstated by ~0.18% per futures trade (0.09% spot). Fixed: TP1 deducts entry+exit costs (×2 sides); TP2 deducts exit cost only (entry was counted at TP1).
- **Session threshold bump could fall below floor** (`signals/engine.py`): US session bump (−0.25) applied to an already-floored adaptive threshold (e.g. 4.0) would produce 3.75 — below `SPOT_THRESHOLD_MIN`. Fixed with `max(..., 3.0)` floor after all adjustments.
- **`max_position_hours_spot` too tight** (`config.py`): 48h = 12 candles on 4H, forcing TIME_EXIT before a typical swing trade resolves. Raised to 72h (18 candles) — same as futures.
- **Funding exit thresholds not symmetric** (`config.py`): `close_short_rate` was −0.08 while `close_long_rate` is +0.10. Comment claimed symmetry. Fixed to −0.10.
- **`SIGNAL_MAX_SCORE` / `SPOT_MAX_SCORE` too low** (`config.py`): Gold/VIX bonus conditions (+0.25 each) added since initial calibration were not reflected in the max score constants, causing display to show >100% in extreme conditions. Updated: 22.25→22.75 (futures), 18.75→19.25 (spot).

---

## 2026-05-11 — fix: HTF scoring ignores direction — SELL signals systematically suppressed

### Fixed

- **`_htf_score` ignores HTF direction** (`signals/engine.py`): When HTF was bearish-aligned (4H=BEARISH, 1D=BEARISH), `_htf_score('BUY')` still returned 1.0–1.5 because the function only checked `htf['aligned']` without verifying the aligned direction matched the signal direction. Fixed: aligned bonus is now only granted when `aligned_dir == 'BULLISH'` for BUY or `aligned_dir == 'BEARISH'` for SELL.
- **`elif sell_add > 0` silently discards SELL HTF score** (`signals/engine.py`): Because `buy_add` was always > 0 in bearish HTF (due to the bug above), the `elif` branch for sell was never reached. Combined effect: in bearish-aligned HTF, `buy_conditions` incorrectly received +1.0–1.5 and `sell_conditions` received +0 instead of +1.75–2.0. Fixed: the `elif` is now a dominant-side comparison (`buy_add > sell_add` vs `sell_add > buy_add`).

---

## 2026-05-11 — fix: Telegram misleading "news downgrade" label on directional conflict

### Fixed

- **Telegram conflict display** (`run_bot.py`, `notifier/telegram.py`): When SPOT and FUTURES signals are opposing (BUY vs SELL), both are suppressed to HOLD. Previously the HOLD header in Telegram incorrectly showed "news downgrade" or "gap 0.00 READY" because the `gap < 0` branch fired (score already exceeded threshold). Now both compact and detailed HOLD formatters check for `signal["_conflict"]` and display `⚠️ CONFLICT` with the actual conflicting directions.

---

## 2026-05-10 — feat: Telegram detail — technicals, HTF, reasons, Gold/VIX

### Added

- **Technicals section per pipeline** (`notifier/telegram.py`): New `━━━ 🔬 TECHNICALS ━━━` block for both SPOT 4H and FUTURES 1H showing RSI (with OB/OS tag), MACD + signal, StochRSI, VWAP, ATR, OBV slope, RSI divergence, candlestick pattern, and Regime/ADX.
- **HTF Alignment section** (`notifier/telegram.py`): New `━━━ ⏱ HTF ALIGNMENT ━━━` block showing per-timeframe trend, RSI, MACD direction, volume trend, and aligned/diverging status for each pipeline.
- **Reasons list in VERDICT** (`notifier/telegram.py`): Up to 12 scoring reasons per mode now shown under the verdict (✅/❌/⚠️ icons). Previously reasons were absent from the consolidated message.
- **Gold & VIX in Market Structure** (`notifier/telegram.py`, `signals/futures.py`, `signals/spot.py`): Gold price (GC=F) and CBOE VIX now fetched in parallel and displayed in the Market Structure section. Both pipelines add `gold` and `vix` keys to `market_structure`.

---

## 2026-05-10 — fix: Gold & VIX data sources + Gold risk-on scoring

### Fixed

- **Gold data source** (`signals/market_data.py`): Replaced PAXG (PAX Gold crypto token) with Yahoo Finance `GC=F` (gold futures). PAXG had crypto-market noise independent of actual gold prices.
- **VIX data source** (`signals/market_data.py`): Replaced `volatility-index-token` (invalid CoinGecko crypto token) with Yahoo Finance `^VIX` (CBOE VIX). Previous implementation returned crypto token price, not the actual equity volatility index.

### Added

- **Gold falling = risk-on buy signal** (`signals/engine.py`): Gold price dropping >0.5% now adds `buy_conditions += 0.25` with reason note. Previously only Gold rising + BTC falling was scored (sell side) — the bullish counterpart was missing.

---

## 2026-05-10 — feat: Condition #19 — Candlestick Pattern Recognition

### Added

- **Candlestick pattern recognition — Condition #19** (`signals/indicators.py`, `signals/engine.py`): New `detect_candlestick_pattern(df)` detects 4 bullish and 4 bearish reversal patterns from the last 3 OHLCV candles. Only the highest-weight pattern per direction is counted (no stacking). Bearish patterns are only scored in futures mode (spot is BUY-only).
  - Bullish: ENGULFING (+1.0), MORNING_STAR (+1.0), HAMMER (+0.75), HARAMI (+0.5)
  - Bearish: ENGULFING (+1.0), EVENING_STAR (+1.0), SHOOTING_STAR (+0.75), HARAMI (+0.5)
- **Max scores updated** (`config.py`): `SPOT_MAX_SCORE` 17.75 → 18.75, `SIGNAL_MAX_SCORE` 21.25 → 22.25

---

## 2026-05-10 — Low Priority Fixes: Backtest Parity, Adaptive Threshold, Wyckoff, S/R Recency

### Changed

- **Backtest trail now matches live paper.py behavior** (`backtest.py`): `_simulate_forward()` reads `trailing_post_tp1_factor` (0.8) and `trailing_advance_min_ratio` (0.5) from `RISK_CONFIG`, applying the same minimum-advance gate and post-TP1 tightening as `paper.py`. Previously backtest trail was always identical to paper.py from before the critical fixes — results were now diverging.
- **Backtest futures funding exit proxy** (`backtest.py`): Simulates a FUNDING_EXIT when unrealized gain exceeds 12% on a futures position (proxy for crowded funding regime). Historical funding data is unavailable in backtest, but extreme sustained moves strongly correlate with positive funding. Applies to both BUY and SELL directions before partial TP.
- **Adaptive threshold: 24h fast window added** (`signals/market_data.py`): `_get_adaptive_threshold()` now checks a 24h window first: if ≥4 signals fired AND win rate < 30%, threshold raises +1.0 immediately (vs the slow 72h raise of +0.5/0.75). Addresses lag where a losing streak on day 1-2 wasn't reflected until day 4. 72h standard window unchanged.
- **Wyckoff Effort vs Result thresholds relaxed** (`signals/engine.py`): Volume climax threshold lowered from `2.0×` → `1.5×` avg and range threshold from `< 0.5×` → `< 0.75×` ATR, making the pattern fire more often. Added directional confirmation: accumulation requires green close (close ≥ open), distribution requires red close. Close position thresholds widened to 0.40/0.60 from 0.35/0.65.
- **S/R detection returns nearest level (not farthest) + recency preference** (`signals/indicators.py`): Previous code sorted resistance descending and returned the highest (furthest) level — now returns the lowest resistance above close (nearest). Among pivots within one ATR band of the nearest level, the most recent pivot is preferred. Same logic applied to support. This corrects both the direction bug and stale-pivot preference.

---

## 2026-05-10 — Medium Quality Fixes: Daily Limit, HTF Volume, TP2 Resistance, Sentiment Freshness, Divergence ATR

### Fixed

- **Daily loss limit circuit breaker now enforced** (`trading/history.py`, `run_bot.py`): Added `get_daily_pnl()` to history.py (sums closed P&L since UTC midnight). `run_bot.py` now checks `daily_pnl < -daily_loss_limit` (default 5%) alongside the existing drawdown check. Both conditions block new entries and send a Telegram alert. Previously `RISK_LIMITS["daily_loss_limit"]` was defined but never used.
- **HTF volume trend uses EWM instead of SMA** (`signals/htf.py`): `_htf_indicators()` now computes `vol_ema5` and `vol_ema20` via `ewm(span=…, adjust=False)`. The previous `rolling(20).mean()` and `tail(5).mean()` gave equal weight to candles 20 weeks ago (on 1W) and last week — EWM weights recent volume higher, making trend detection more responsive.
- **TP2 capped below nearest resistance (BUY) / above support (SELL)** (`signals/engine.py`): If `support_resistance` contains a level between entry and the raw TP2, TP2 is set to `resistance × 0.995` (or `support × 1.005` for shorts). Prevents setting aggressive TP2 targets past a strong structure level that typically absorbs price.
- **News sentiment ignores articles older than 24h** (`signals/sentiment.py`): CSV is now filtered to rows with `timestamp >= now - 24h` before the `head(7)` selection. If scraper stalled, stale headlines no longer bias the combined sentiment score. Rows are sorted newest-first so the 7 freshest articles are always used.
- **RSI divergence threshold scales with ATR** (`signals/indicators.py`): Fixed `threshold = 0.002` replaced with `max(0.002, ATR / price)`. At BTC $100k with ATR $200 (0.2%), threshold stays at 0.2%. At high volatility with ATR $500 (0.5%), threshold rises to 0.5% — preventing noise pivots from triggering false divergence signals.

---

## 2026-05-10 — Critical Quality Fixes: Trail Noise, HTF False Positives, Confidence Staleness, Regime Filter

### Fixed

- **Trailing stop no longer ratchets on micro-moves** (`trading/paper.py`, `config.py`): Trail only advances when the new level is at least `ATR × trail_factor × 0.5` above the current trail. Previously, any single-candle close slightly above trail would update it, causing gradual ratcheting in sideways markets and premature stop-outs on normal pullbacks. Added `trailing_advance_min_ratio: 0.5` to `RISK_CONFIG`.
- **Trailing stop tightens 20% after TP1 hit** (`trading/paper.py`, `config.py`): After the first 50% partial close, the remaining position uses `trail_factor × 0.8` instead of the full factor. At half position size the risk profile is lower, so a tighter trail protects the accumulated gain more aggressively. Added `trailing_post_tp1_factor: 0.8` to `RISK_CONFIG`.
- **HTF aligned flag no longer false-positive on momentum exhaustion** (`signals/htf.py`): `aligned=True` now requires that the two HTF timeframes are NOT both in an extreme counter-trend RSI zone. Example: 4H BULLISH + 1D BULLISH both overbought → `aligned=False` (impending reversal, not a safe buy setup). Both `get_htf_trend()` (futures) and `get_spot_htf_trend()` (spot) updated via shared `_htf_aligned()` helper.
- **Confidence recalculated after news integration** (`signals/engine.py`): `integrate_news_with_signal()` now calls `get_signal_confidence()` at the end to reflect post-news strength. Previously a signal that dropped from strength 6.5 → 4.2 after news was still labeled "STRONG", allowing pyramid entries to open on stale confidence.
- **Regime filter only blocks TRENDING + BEARISH entries** (`run_bot.py`): `_is_bearish_regime()` now requires `regime == "TRENDING"` in addition to `trend_dir == "BEARISH"`. In ranging/transition markets (ADX < 20-25), DI- > DI+ is normal oscillation — blocking spot BUY in these conditions was incorrectly rejecting valid pullback entries.

---

## 2026-05-10 — Strategy Tuning: 10 Parameter Fixes (Vol Exit, Trail, OBV, S&P, VWAP, Funding, Time Exit, EMA Slope, BB Squeeze, Pyramid SL)

### Changed

- **EMA 200 condition now includes slope** (`signals/engine.py`): Condition #1 differentiates between price above a rising EMA (full 1.0 pt) vs price above a flat/falling EMA (0.5 pt). Previously, a bullish position in a months-long uptrend always scored 1.0 regardless of EMA momentum. Uses a 5-candle slope to avoid single-candle noise.
- **Bollinger Band middle zone replaced with squeeze detection** (`signals/engine.py`): The unconditional ±0.25 "price above/below BB middle" score is replaced with a volatility compression check. Score only awarded when the current BB width is in the bottom 30th percentile of the past 20+ candles (squeeze), combined with price direction vs middle. This was previously firing on almost every non-extreme candle.
- **Pyramid SL tightening: multiplicative → additive ATR-based** (`run_bot.py`, `config.py`): SL per pyramid level now uses `entry - atr × max(1.0, 1.5 - 0.25 × (n-1))` instead of `sl_dist × 0.8^(n-1)`. Results: Entry #2 = 1.25× ATR (was 0.8×), Entry #3 = 1.0× ATR (was 0.64×). The multiplicative formula compounded to below-wick levels at entry #3; the additive formula has a hard 1.0× ATR floor. Added `tighten_sl_atr_step: 0.25` to pyramid config.

---

## 2026-05-10 — Strategy Tuning: 7 Parameter Fixes (Vol Exit, Futures Trail, OBV, S&P, VWAP, Funding, Time Exit)

### Changed

- **S&P500 weight halved for spot mode** (`signals/engine.py`): S&P500 bias now scores 0.5 (spot) vs 1.0 (futures). BTC-SPX correlation weakens during crypto-driven cycles; 1.0 was equivalent to a MACD crossover — too high for an external macro factor. `SPOT_MAX_SCORE` updated 18.25 → 17.75.
- **VWAP requires recent crossover** (`signals/engine.py`): Condition #17 now only scores when price crossed the VWAP in the last 5 candles (was below/above within lookback window). Pure "price above VWAP" in a sustained trend no longer awards 0.75 automatically — that was effectively a free score in any uptrend.
- **Funding exit short threshold symmetric** (`config.py`): `close_short_rate` raised from `-0.05%` → `-0.08%`. The previous asymmetry (long closed at >0.10%, short closed at <-0.05%) was treating shorts as far more sensitive than longs. `-0.08%` is more proportional.
- **Spot time exit reduced to 48h** (`config.py`, `trading/paper.py`, `backtest.py`): Added `max_position_hours_spot: 48`. BTC 4H signals typically materialize within 12-15 candles (48-60h); holding to 72h locks capital in declining-quality setups. Futures unchanged at 72h. Both paper.py and backtest.py now read mode-aware values.

---

## 2026-05-10 — Strategy Tuning: 3 Parameter Fixes (Vol Exit, Futures Trail, OBV Filter)

### Changed

- **Vol expansion exit threshold tightened** (`config.py`): `vol_expansion_exit_mult` reduced from `2.0` → `1.5`. BTC 4H ATR can spike 1.8-2.0× in a single candle during news events, meaning the 2.0× exit was triggering *after* damage already occurred. 1.5× catches exhaustion earlier while still filtering noise.
- **Futures trailing stop loosened** (`config.py`): `FUTURES_CONFIG["trailing_atr_factor"]` raised from `0.7` → `0.9`. At 0.7× ATR on 1H candles, normal wicks frequently triggered premature trail exits — the tighter trail was intended to reflect leverage amplification, but 0.7× is below typical wick size on BTC 1H. Spot trail unchanged at 1.0×.
- **OBV activation threshold tightened** (`signals/engine.py`): OBV signal threshold raised from `obv_rel >= 0.001` → `>= 0.002`. The 0.001 threshold fired on nearly every candle with any volume, making the 0.75-point OBV condition a near-automatic score contribution. 0.002 requires meaningful net OBV flow relative to 5-candle volume.

---

## 2026-05-10 — Pyramid Strategy Review: 2 Bug Fixes (Cache Mutation, P&L Weighting)

### Fixed

- **Spot signal cache no longer mutated by run_bot** (`signals/spot.py`): `analyze_spot_signal()` was returning the cached dict reference directly. `run_bot.py` mutates `spot_signal["type"]`, `stop_loss`, `take_profit`, and `tp2` at multiple points (HOLD forcing, SL tightening for pyramid entries). Subsequent bot cycles within the same 4H candle received a corrupted cached signal — e.g. a forced HOLD would suppress valid signals for the rest of the candle, and pyramid SL tightening would compound on itself each cycle. Fixed by returning `dict(_spot_cache["signal"])` (shallow copy) on cache hit.
- **Pyramid P&L now weighted by size_factor** (`trading/history.py`): `close_paper_position()` was storing raw `pnl_pct` for all positions regardless of pyramid size. Entry #3 (25% of base size) was reporting the same percentage contribution as Entry #1 (100%), inflating aggregate P&L stats and win-rate calculations. Fixed in `close_paper_position()` by multiplying `pnl_pct` by the position's stored `size_factor` (1.0 for initial entries, 0.5/0.25 for pyramid entries) before storing.

---

## 2026-05-10 — Full Codebase Review: 3 Bug Fixes (Paper Trail Fee, Sizing Threshold, Docstring)

### Fixed

- **`paper.py` trailing stop exits now apply exit fees** (`trading/paper.py`): BUY and SELL trailing stop exits were computing P&L directly (`(trail - entry) / entry`) without fees. All other exit types (MACRO_CLOSE, TIME_EXIT, VOL_EXIT, FUNDING_EXIT) use `_calc_pnl()` which subtracts `(fee + slippage)`. Both trail branches now call `_calc_pnl(pos, trail)` for consistency — trail exits were overstating P&L by ~0.15% (futures) or ~0.30% (spot) per trade.
- **Spot position sizing ATR percentile thresholds use `>=` consistently** (`signals/sizing.py`): spot used strict `>` while futures used `>=`, causing a one-position edge-case discrepancy at exactly `atr_pct = 0.90` and `0.75`. Both now use `>=`.
- **`spot.py` docstring updated to match actual behavior** (`signals/spot.py`): docstring claimed "15 conditions (no funding/L/S/OI/basis)" but the pipeline fetches funding rate and L/S ratio, which engine.py uses at ½-weight (0.25 each) for spot. Docstring now accurately describes behavior; OI and basis remain excluded.

---

## 2026-05-10 — Technical Indicator Review: 4 Correctness Fixes (ADX, Backtest HTF)

### Fixed

- **ADX minus DM formula corrected** (`signals/indicators.py`): was using `low.diff().abs()` (always positive) causing false -DM readings on gap-up days. Fixed to `-low.diff()` so -DM only fires when price actually moves down, matching Wilder's canonical definition.
- **Backtest HTF MACD now uses actual MACD** (`backtest.py`): was comparing `ohlc[-1] > ohlc[-2]` (price direction) instead of EMA12−EMA26 vs signal line EMA9. Now calls `calculate_macd()` from `signals/indicators.py` — same function used by the live engine.
- **Backtest HTF RSI now uses 5-zone classification** (`backtest.py`): was using 3 zones (oversold/neutral/overbought) while live `htf.py` uses 5 (oversold/low/neutral/elevated/overbought). Unified to match live behavior.
- **Backtest HTF RSI now uses Wilder's algorithm** (`backtest.py`): was using EWM approximation directly on gain/loss. Now calls `calculate_rsi()` from `signals/indicators.py` — same Wilder's iterative smoothing used by the live engine.

---

## 2026-05-10 — Futures Strategy Review: 3 Bug Fixes (Backtest Double-Bump, OI Asymmetry)

### Fixed

- **Backtest no longer double-applies regime + session threshold bumps** (`backtest.py`): `effective_threshold` was computed as `base + regime_bump + session_bump` before being passed to `generate_signals()`, which then added them again internally (regime from same window = identical value; session from `datetime.now()` = wrong historical time). Backtest now passes just the base adaptive threshold; the engine applies both adjustments exactly once.
- **OI×Price bear case now symmetric with bull case** (`signals/engine.py`): `OI↑+Price↓` (new shorts opening as price falls = confirmed distribution) was scored `+0.5` sell while the mirror condition `OI↑+Price↑` scored `+0.75` buy. Updated to `+0.75` — equal information strength in opposite directions.
- **`SIGNAL_MAX_SCORE` comment updated** (`config.py`): replaced stale calculation comment with accurate description (practical max ~21.25 after diminishing-returns penalty; theoretical ceiling ~22).

---

## 2026-05-10 — Spot Strategy Review: 5 Bug Fixes (Scoring, Backtest Fees, Gates)

### Fixed

- **RSI divergence no longer double-inflates scores** (`signals/engine.py`): when bullish divergence fires against an overbought RSI, the OB sell score is now cancelled (−1.5) before adding to buy; previously only the buy side increased, leaving sell inflated. Same fix applied for bearish divergence + oversold RSI.
- **RSI divergence now immune to diminishing-returns penalty** (`signals/engine.py`): divergence scoring block moved to after the correlated-extremes penalty block. Divergence measures price-RSI momentum — structurally independent from RSI/BB/StochRSI price extremes — so it should not be discounted when 3 extremes cluster. Both old blocks (early scoring + override) replaced with a single consolidated post-penalty block.
- **Backtest trailing stop exits now deduct exit fees** (`backtest.py`): trailing stop P&L was computed inline without any exit cost. Now applies `(fee_pct + slippage)` to the trail price before computing P&L, consistent with how TP targets are fee-adjusted.
- **Backtest TIME_EXIT / VOL_EXIT now use mode-aware taker fee** (`backtest.py`): `_calc_backtest_pnl` was applying slippage-only on exit (comment read "fee already in entry"). Updated to apply the correct taker fee per mode (0.10% spot, 0.04% futures) plus slippage on exit, matching the live `trading/paper.py` cost model.
- **Backtest spot entry gates now include psychology SL check** (`backtest.py`): added gate matching live `run_bot.py` behavior — skips entry if SL is within 0.15% below a $1,000 round number (stop-hunt zone).
- **`SPOT_MAX_SCORE` comment updated** (`config.py`): updated value to 18.25 and corrected comment; previous value (17.25) was an underestimate from an older condition set.

---

## 2026-05-09 — Strategy Overhaul: Signal Quality, Risk Gates, Exit Conditions, Formula Fixes

### Added

- **5 signal quality improvements** (`signals/engine.py`, `signals/spot.py`, `signals/market_data.py`):
  - **Diminishing returns** on correlated OS/OB conditions (RSI+BB+StochRSI): 1st full weight, 2nd −0.75, 3rd −1.5
  - **RSI divergence priority** — suppresses contradictory RSI zone score (divergence is stronger)
  - **Spot vs Futures directional conflict** — when spot=BUY and futures=SELL, both downgraded to HOLD
  - **4H candle caching** (`signals/spot.py`) — returns cached result within same candle, saves 75% API calls
  - **Quality-aware adaptive threshold** (`signals/market_data.py`) — win rate <35% → +0.75 raise; ≥60% → −0.5 drop
- **6 medium-impact strategy improvements**:
  - **Time-based exit** (`trading/paper.py`) — force-close positions older than `max_position_hours` (72h)
  - **Vol-expansion exit** — close if current ATR > entry ATR × `vol_expansion_exit_mult` (2.0×)
  - **First-entry TA gates** (`run_bot.py`) — added psychology SL + S/R proximity to first entry (was 3, now 5 gates)
  - **S/R proximity ATR-scaled** (`signals/engine.py`) — 0.2× ATR replaces hardcoded 0.3%
  - **F&G contradiction** — BUY into GREED (≥70) or SELL into FEAR (≤30) penalized −0.5
  - **HTF scoring** — aligned ≥1.0, diverging ≤0.5 (was both 0.75)
  - **Funding vs L/S tie-breaking** — net weight to dominant side on conflict
- **Futures entry safety gates** (`run_bot.py`) — confidence floor (NORMAL), fakeout rejection, re-entry quality, aggregate risk cap (8%). Flip path also gated.
- **Funding-based exit** (`trading/paper.py`) — close LONG if funding >0.10%, close SHORT if ←0.05%
- **ADX/DI trend strength** (`signals/indicators.py`, `signals/engine.py`) — condition #18: ADX >25 trending +0.5, DI+/DI- crossover +0.75. Replaces binary EMA200 with proper trend quantification.
- **Regime classifier** (`signals/indicators.py`) — TRENDING (ADX>25, threshold −0.25), RANGING (ADX<20, +0.5), VOLATILE (ATR>90th, +0.25, size×0.75), TRANSITION (normal)
- **OI×Price directional** (`signals/engine.py`) — OI↑+Price↑ = healthy uptrend (+0.75), OI↑+Price↓ = distribution (+0.5), OI↓+Price↑ = short squeeze (+0.25), OI↓+Price↓ = liquidation cascade (+0.5)
- **Session-based threshold** (`signals/engine.py`) — Asia (0-7 UTC) +0.5, US (13-22 UTC) −0.25
- **4 futures strategy improvements**:
  - **F&G symmetry** (`signals/sizing.py`) — SELL at extreme greed (≥80) gets +0.15 (was BUY-only)
  - **Flip profitability gate** (`run_bot.py`) — block flip if total loss > expected reward
  - **Proper liquidation formula** — `entry × (1 - (1 - mm_rate) / leverage)` replaces 0.95 fudge
  - **Futures trailing stop** — 0.7× ATR (was 1.0× shared with spot)
- **News improvements** (`news_scraper.py`):
  - **Freshness filter** — drop headlines older than 24h via RSS pubDate parsing (~40% reduction)
  - **Sentiment normalization** — `score / √word_count` bounds raw keyword count by headline length
- **Spot improvements**:
  - **Volatility-adjusted sizing** (`signals/sizing.py`) — max_position_size scaled by ATR percentile (matching futures)
  - **ATR percentile in spot signal** (`signals/spot.py`) — was futures-only
  - **Futures sentiment into spot** (`signals/spot.py`, `signals/engine.py`) — funding + L/S as lightweight sentiment (0.25 weight each)
- **Max scores updated**: Futures 19.25→21.25, Spot 15.5→17.25

### Fixed

- **Wilder's RSI formula** (`signals/indicators.py`) — replaced Cutler's SMA (`rolling(window).mean()`) with proper Wilder's EMA (`avg = (prev × 13 + current) / 14`). Affects RSI, StochRSI, divergence detection.
- **Wilder's ATR formula** (`signals/indicators.py`) — replaced SMA with `ewm(alpha=1/period)`. Now consistent with ADX smoothing.
- **3 code bugs**: `_CURRENT_ATR` NameError (crash on vol-exit), `_last_atr` missing `global` (mid-cycle vol-exit disabled), `threshold=None` TypeError
- **Undefined `last_entry`**, KeyError risk in gate messages, redundant re-imports

### Removed

- **Post-pyramid 12h cooldown** — redundant with TA-driven `_check_reentry_quality()` which already blocks re-entry when price is worse with no confidence upgrade

---

## 2026-05-09 — Dynamic Leverage + Bug Fixes + Polish

### Added

- **Spot pyramiding** (`run_bot.py`, `config.py`, `signals/sizing.py`, `trading/history.py`, `trading/paper.py`, `notifier/telegram.py`) — when a spot BUY signal fires with STRONG confidence and a BUY position is already open, opens an additional pyramid entry instead of skipping. **8 safety gates**: (1) max entries, (2) ordinal confidence ≥STRONG, (3) min ATR distance from last entry, (4) max % distance from 1st entry, (5a) SL near psychology level, (5b) entry just above round number, (6) entry near S/R, (7) fakeout/rejection wick, (8) aggregate risk cap. Each pyramid entry gets progressively tighter SL (0.8× per level) and its own TP/trailing stop. DB columns `pyramid_entry` + `size_factor`. Terminal shows `[pyramid #N ×50%]` tag; Telegram uses 🧩 icon.
- **`get_open_position_count_by_direction()`** (`trading/history.py`) — returns count of open positions for a given direction and mode.
- **`get_pyramid_size_factor()`** (`signals/sizing.py`) — returns exponential size multiplier for pyramid entry N (1.0 → 0.5 → 0.25 → …).
- **`_get_entry_prices_by_direction()`** (`run_bot.py`) — returns ordered entry prices for open positions, used by distance guards.
- **TA-based pyramid risk gates** (`run_bot.py`) — five new risk checks before pyramiding: (5a) SL near psychology number → stop-hunt risk, (5b) entry just above psychology level → false breakout risk, (6) entry within 1× ATR of resistance (BUY) / support (SELL) → rejection risk, (7) fakeout detection via 24H wick ratio > 60% → reversal signal, (8) aggregate risk cap across all entries (default 5% of account).
- **`_check_psychology_sl_risk()` / `_check_psychology_entry_risk()`** — round-thousand proximity checks.
- **`_check_sr_entry_risk()`** — S/R proximity risk scoring (within 1× ATR = elevated rejection risk).
- **`_detect_fakeout_rejection()`** — 24H wick analysis: if upper wick > 60% of range for BUY, flags fake bullish breakout.
- **`_calc_aggregate_risk()`** — sums risk % weighted by size_factor across all open + new positions.
- **Conviction-based dynamic leverage** (`signals/sizing.py`) — 6-factor model replaces static leverage formula. Leverage now scales with signal confidence: strength ratio vs threshold, HTF alignment, RSI zone confirmation, funding rate, Fear & Greed contrarian, and volatility regime. `_compute_confidence()` returns 0.25–1.5 multiplier. ATR percentile caps max leverage in high-vol regimes (0.33x–1.0x). Effective risk = base_risk × confidence × vol_cap. Tier labels: CONSERVATIVE (≤3x), MODERATE (≤6x), AGGRESSIVE (≤10x).
- **ATR percentile** (`signals/indicators.py`) — `compute_atr_percentile()` ranks current ATR in 100-period history. Used by dynamic leverage.
- **LEVERAGE_CONFIG** (`config.py`) — new config dict for base_max_leverage, atr_lookback, fractional_kelly, confidence bounds.

### Fixed

- **Pyramid confidence check used exact match** (`run_bot.py`) — `!=` comparison meant `min_confidence: "NORMAL"` would skip STRONG signals. Replaced with ordinal ranking (`_CONFIDENCE_LEVEL` + `_confidence_at_least()`): STRONG > NORMAL > WEAK.
- **Post-news strength below threshold still fired** (`signals/engine.py`) — `integrate_news_with_signal()` drains strength (macro -2.0, contradictory news -0.5) but never re-validated against threshold. Now stores `_threshold` in `generate_signals()` and downgrades to HOLD if post-news `strength < threshold`.
- **Futures leverage always 1x** (`signals/sizing.py`) — broken formula `int(1 / (sl_pct * 100))` always returned 0 for realistic stops (>0.5%), clamped to 1x. Replaced with risk-based calculation: `needed_position = risk_amount / sl_distance_pct`.
- **Backtest crash** (`backtest.py`) — `generate_signals()` called without `threshold_override=None`, causing `float >= None` TypeError. Now passes `SIGNAL_THRESHOLD`.
- **Per-mode stats were global** (`trading/history.py`) — `get_outcome_breakdown()`, `get_win_rate()`, `get_profit_factor()` now accept optional `mode` parameter. `print_paper_summary()`, `print_open_status()`, Telegram PERFORMANCE, and combined display all pass per-mode filtering. Previously SPOT and FUTURES showed identical global W/L counts.
- **Negative gap display** — when news downgraded a signal, gap showed as negative (`-2.10 to fire`). Now displays "news downgrade → HOLD" in both terminal and Telegram.

### Changed

- **Telegram notification order** (`run_bot.py`) — main signal alert now sent first, position open/close/warning follow. Previously position notifications fired during Phase 3 before the main signal.
- **Position open gets dedicated Telegram card** (`notifier/telegram.py`) — `_format_open_notification()` sends vertical card with entry, SL%, TP1, TP2, R/R when position opens. Separate from main signal.

### Removed

- **Discord** — `notifier/discord.py` deleted. All Discord references removed from `config.py`, `notifier/common.py`, `notifier/__init__.py`, `notifier.py` shim. Discord webhook logic was unused and added maintenance burden.

---

## 2026-05-08 — Combined Terminal Display + Per-Mode Cleanup

### Added

- **Combined SPOT + FUTURES terminal display** (`signals/terminal.py`) — `display_combined()` replaces two separate `display_analysis()` calls with a single narrow (52-char), fully vertical output. Shared sections (market structure, sentiment, performance) appear once. Mode-specific sections (technicals, HTF, trade setup, reasons) stack vertically per mode. Old `display_analysis()` kept for backward compat with `display=True` kwarg.
- **Mid-cycle check header** (`run_bot.py`) — `run_position_check()` now prints clean separator with BTC price.

### Changed

- **VERDICT section** — terminal and Telegram both use 🔥 (FIRED) / ❄️ (HOLD) icons, buy/sell score breakdown (`B:7.10 · S:2.80`), threshold comparison. Replaced old bar charts and verbose NOTE format.
- **_signal_box()** — boxed verdict now includes buy/sell scores, 🔥/❄️ icons, per-mode max scores.
- **Phase 3 summary** (console) — cleaner header, 🚀 icon for opens, structured block.
- **print_open_status()** — multi-line per position format with SL, TP1, TP2, Trail, Opened.
- **Label consistency** — `FUT 1H` standardized to `FUTURES 1H` across all display sections.

---

## 2026-05-08 — Cron-scheduling fix + heartbeat logs

### Fixed

- **Cron loop skipped cycles after first hour** (`run_bot.py`) — `fired` set was never cleared between hours. After `:01` and `:31` fired, the next hour's `:01` was incorrectly skipped (`1 in fired`). Fixed by clearing `fired` on every wall-clock minute change using a `last_minute` tracker.
- **No heartbeat during wait** — added log messages: "=== Full cycle starting ===" before run_cycle, and "Next run at :XX (~Y min)" after each cycle/check so the log shows the bot is alive between scheduled runs.

---

## 2026-05-07 — SPOT BUY-Only + Clean Vertical Telegram Format

### Changed

- **Consolidated Telegram notifications into single message** (`notifier.py`) — new `_format_consolidated_telegram()` builds one comprehensive message per cycle (was 3-5 separate messages). Sections: Signal Verdicts (both SPOT + FUTURES), Price & Trend, Market Structure, Top Headlines, Performance (all-time paper, per-mode P&L + WR), Position Sizing (SPOT & FUTURES with active trade details or hypothetical SL/TP), NOTE (per-mode verdict summary). Only macro risk banner and position close alerts remain as separate messages.
- **Removed `_format_position_telegram()`** — position/performance summary merged into consolidated message.
- **`_send_combined_telegram()` simplified** — now sends 1 main message + optional macro banner instead of 3-5 separate messages.
- **SPOT pipeline now BUY-only** (`core_analysis.py`) — `generate_signals()` prevents SELL signals when `mode='spot'`. Spot trading cannot short-sell; SELL conditions still show in analysis for informational purposes but are forced to HOLD. A note "SPOT is BUY-only — bearish bias, no SELL opened" appears in signal reasons when bearish conditions dominate.
- **Terminal NOTE section updated** (`core_analysis.py`) — SPOT HOLD with bearish bias now shows "BEARISH" instead of "SELL" as direction, "SPOT is BUY-only" instead of "sell side overrides". WHAT THIS MEANS section clarifies no action is taken on bearish spot signals.
- **Telegram format redesigned — clean vertical layout** (`notifier.py`) — `_format_compact_signal_telegram()` rewritten: every indicator on its own line (`Label    Value`), separate sections for Price & Trend, Technicals, HTF, Market, Sentiment, and Reasons. HTF reads raw indicator dicts for accurate RSI/MACD/Volume per timeframe. Previously all crammed horizontally with `·` separators — now scannable on mobile.
- **Telegram HOLD cards** (`notifier.py`) — spot HOLD shows "BEARISH" as direction (not "SELL"). Futures HOLD unchanged.
- **`run_bot.py`** — comment updated from "max 1 BUY + 1 SELL" to "BUY-only (no short selling on spot)".
- **README** — SPOT BUY-only documented in architecture diagram, position management, and position sizing sections.
- **Local timezone** (`core_analysis.py`) — analysis header now uses system local time (e.g. WIB) instead of hardcoded UTC. `datetime.now().astimezone().strftime(...)`.

### Fixed

- **Telegram not updating** — `send_signal_alert()` was calling `_format_compact_signal_telegram()` (the actual sending function), but the initial rewrite only touched `_format_section_telegram()` which was unused. Second pass rewrote the correct function.

---

## 2026-05-06 — HTF Analysis Strengthened + Cycle Logging + SPOT HOLD Notifications

### Added

- **Enhanced HTF multi-timeframe analysis** (`core_analysis.py`) — each higher timeframe now computes 4 indicators (not just EMA200): RSI with zone classification, MACD direction, volume trend vs 20-period avg, and price distance from EMA200. `_htf_indicator()` helper extracts all indicators from OHLCV data. HTF scoring is now nuanced: full weight when aligned + RSI confirms, reduced when RSI warns, MACD bonus +0.25, volume bonus +0.25, and diverging HTFs with extreme RSI contribute reversal signals (+0.75). Max HTF score: 2.0 (was 1.5).
- **`cycle_log` table** (`signal_history.py`) — new SQLite table recording every analysis cycle (including HOLD) with 35+ fields: scores, all technical indicators, full market structure snapshot, HTF data as JSON, sentiment, top reasons, and open position count. `log_cycle()` called after each `analyze_*_signal()`. Enables post-hoc analysis of signal quality and threshold tuning.
- **SPOT HOLD notifications** (`notifier.py`) — HOLD signals are now sent to Telegram (previously silent). HOLD cards include gap-to-fire info showing how close the signal is to firing and which direction leads.
- **Position limit clarified** (`config.py`, `run_bot.py`) — log messages now say "max 1 BUY + 1 SELL per mode" instead of generic "max positions reached".

### Changed

- **HTF terminal display** (`core_analysis.py`) — MULTI-TIMEFRAME section now shows RSI value/zone, MACD direction, and volume trend per timeframe alongside trend bias.
- **HTF in notifications** (`notifier.py`) — compact signal card includes RSI + MACD per HTF in the technicals section.
- **Max scores updated** — `SIGNAL_MAX_SCORE`: 18.75 → 19.25, `SPOT_MAX_SCORE`: 15.0 → 15.5 (enhanced HTF adds 0.5).

### Config

- **README rewritten** — full architecture flow diagram, 18 conditions table, HTF analysis detail, macro handling, position management, notification structure, cycle_log documentation with SQL queries, testing guide.

---

## 2026-05-06 — Clean Notification Sections + Position Close Alerts

### Changed

- **Signal card redesigned with section headers** (`notifier.py`) — sections now grouped under emoji headers: 📊 Trade, 📈 Technicals, 🏦 Market, 📰 Sentiment, ✅ Reasons. Each section on its own line group for easy scanning.
- **Position close notifications** (`notifier.py`, `paper_trader.py`, `run_bot.py`) — when positions exit (TP1/TP2/trailing stop/SL/macro force-close), a dedicated Telegram message is sent showing: type, entry→exit, P&L%, and outcome label.
  - `check_and_close_positions()` now returns list of close-event dicts
  - `_format_close_notification()` renders them as compact close cards
  - Phase 3 in `run_bot.py` collects close events and sends notification

---

## 2026-05-06 — Compact Telegram Notifications (Split per Mode)

### Changed

- **Redesigned Telegram format** (`notifier.py`) — single long combined message (~3200 chars) replaced by 2-3 compact separate messages:
  - **Signal card** (~600 chars) per mode (SPOT / FUTURES): verdict, trade setup in code block, technicals one-liner, HTF, market structure, sentiment, top 7 reasons
  - **Position + Performance** (~400 chars): open positions with entry/trail/TP1, closed P&L, outcome breakdown (W/L/MC)
  - **Macro warning** sent as standalone banner when active
- **HOLD signals are silent** — no Telegram message sent when signal is HOLD *(changed 2026-05-06 — HOLD messages now sent)*
- **`_send_telegram_message()`** helper extracted for reusable single-message delivery with label logging
- **`_threshold`** added to signal dict in `core_analysis.py` for compact card display

---

## 2026-05-06 — Terminal Display & Output Improvements

### Changed

- **`print_open_status()`** (`paper_trader.py`) — now shows mode label `[SPOT]` / `[FUTURES]`, outcome breakdown in closed-trades line (`1MC`, `2W · 1L`), and win rate percentage.
- **`print_paper_summary()`** (`paper_trader.py`) — now shows mode label and Outcomes line with WIN/LOSS/MACRO_CLOSE/BREAKEVEN counts.
- **`check_and_close_positions()`** (`paper_trader.py`) — prints a visible terminal banner when macro event force-closes positions (not just log-level).
- **Phase 3 summary** (`run_bot.py`) — all position-management actions are tracked and printed as a summary line at the end of the cycle: `✅ SPOT BUY opened (#N) @ $XX` or `⏭ FUT: Duplicate BUY direction — skipping futures`.

---

## 2026-05-06 — Notifikasi Diperkaya & Position Safety

### Added

- **Open position status in notifications** (`notifier.py`) — Telegram/Discord messages now include a section showing all open positions with entry, trailing stop, and TP1 progress. Also added `_format_position_status()` / `_format_position_status_discord()` helpers.
- **Macro risk warning banner** (`notifier.py`) — when a signal is penalized for an upcoming HIGH impact event, a prominent "MACRO RISK" banner appears at the top of the notification.
- **Outcome breakdown in performance footer** (`notifier.py`) — format `0W · 0L · 1MC` showing WIN / LOSS / MACRO_CLOSE / BREAKEVEN counts plus win rate. `get_outcome_breakdown()` added to `signal_history.py`.
- **Macro-driven position force-close** (`paper_trader.py`) — `check_and_close_positions()` now gates on `check_upcoming_macro_events()`. If a HIGH impact USD event is <2h away, ALL open positions are force-closed at market price with outcome `MACRO_CLOSE`, regardless of mode. Macro risk trumps technical setups.
- **Slippage warnings** (`paper_trader.py`) — `_check_slippage()` logs a warning when fill price is >1% past the trailing stop trigger, making 60-min cycle lag visible in logs.
- **Duplicate same-direction position prevention** (`run_bot.py`, `signal_history.py`) — `has_open_position_same_direction()` checks whether an open position already exists with the same direction before opening a new one. This prevents stacking nearly identical entries when the same signal fires on consecutive cycles.

### Fixed

- **Win rate always None** (`signal_history.py`) — `get_win_rate()` was querying the `signals` table (never populated with outcomes) instead of `paper_positions`. Now queries `paper_positions WHERE outcome IN ('WIN','LOSS')`.

---

## 2026-05-06 — Signal Quality & Performance Metrics Fixes

### Changed

- **RSI Divergence — pivot-based detection** (`core_analysis.py`) — replaced 5-candle lookback with swing pivot detection (50-candle window, 3-candle pivot neighbourhood). The two most recent swing lows/highs are compared; requires >0.2% price difference to filter noise. This is the highest-weight condition (2.0) and was previously prone to false signals from minor price wiggles.
- **Volume climax / Effort-vs-Result** (`core_analysis.py`) — new condition #4b: when volume >2× average AND candle range <50% of ATR, the candle close position determines direction. Close in lower third = accumulation (+0.75 buy), upper third = distribution (+0.75 sell). Classic Wyckoff concept now captured.
- **Macro force HOLD → strength penalty** (`core_analysis.py`) — HIGH impact event within 2h now applies -2.0 strength reduction instead of completely zeroing the signal. Very strong technical setups can still fire with a warning; weak signals that drop to ≤0 are still forced to HOLD.

### Fixed

- **Profit factor now uses actual P&L** (`signal_history.py`) — `get_profit_factor()` was querying theoretical TP/SL distances from the `signals` table instead of realised P&L from `paper_positions`. The displayed profit factor was misleading — a signal with wide TP and tight SL would show high PF even if never reached. Now uses `SUM(pnl_pct)` from actual closed paper positions.
- **Per-mode P&L tracking** (`core_analysis.py`) — `display_analysis()` was splitting total P&L 60/40 (spot/futures) arbitrarily. Now queries `get_closed_pnl(mode='spot')` and `get_closed_pnl(mode='futures')` separately, showing actual per-mode balance changes.
- **Gap to fire shows correct base threshold** (`core_analysis.py`) — NOTE section always referenced `SIGNAL_THRESHOLD` (5.2) even for spot mode (base 4.3). Now uses `SPOT_THRESHOLD` for spot and `SIGNAL_THRESHOLD` for futures.

### Config

- **Max scores updated** — `SIGNAL_MAX_SCORE`: 18.0 → 18.75, `SPOT_MAX_SCORE`: 14.25 → 15.0 (new volume climax condition adds 0.75).

---

## 2026-05-06 — Full-Detail Combined Telegram & Discord Notifications

### Changed

- **`_format_section_telegram()` / `_format_section_discord()`** — Redesigned from compact one-liner to full-detail sections matching terminal output. Both HOLD and non-HOLD signals now show: Price & Trend (price, EMA200, 24h range, S/R), HTF alignment, Technicals (RSI, StochRSI, MACD, VWAP, ATR, OBV, divergence), Market Structure (mode-appropriate fields), Sentiment (F&G, news, top 3 headlines), and Signal Reasons (up to 10). Trade Setup (Entry/SL/TP/RR) only shown for BUY/SELL.
- **Combined message char count** — ~3200 chars for two full sections (SPOT + FUTURES) plus open positions and performance footer, still well under the 4096-char Telegram limit.

---

## 2026-05-06 — Separate Spot & Futures Signal Pipelines

### Added

- **`analyze_spot_signal()`** (`core_analysis.py`) — 4H OHLCV pipeline. Fetches 1D + 1W HTF trend (`get_spot_htf_trend()`), skips futures-only conditions (Funding Rate, L/S Ratio, Open Interest, Futures Basis), uses `VWAP_24` over 6 × 4H candles (= 24H). Adaptive threshold starts at 4.3 (`SPOT_THRESHOLD`), max score 14.25 (`SPOT_MAX_SCORE`).
- **`analyze_futures_signal()`** — renamed from `analyze_btc_signal()` (kept as backward-compat shim). 1H pipeline unchanged: 19 conditions, threshold 5.2, max score 18.0.
- **`get_spot_htf_trend()`** — fetches 1D + 1W EMA200 bias (spot trades on 4H so HTF = daily + weekly).
- **Adaptive threshold per mode** — `get_spot_adaptive_threshold()` / `update_spot_threshold_state()` backed by `spot_threshold_state.json`. Futures uses existing `threshold_state.json`. Both share `_get_adaptive_threshold()` / `_update_threshold_state()` helpers to eliminate duplication.
- **`mode` column in `paper_positions`** — auto-migrated. `open_paper_position(signal, mode='futures')` stores `'spot'` or `'futures'`.
- **Mode-filtered DB queries** — `get_open_positions(mode=None)`, `get_closed_pnl(mode=None)` accept optional mode so spot/futures performance is tracked separately.
- **Combined notifications** — `send_signal_alert(spot_signal, futures_signal)` sends one Telegram/Discord message with both sections. HOLD section shows score only; non-HOLD shows full trade setup. Paper performance footer broken out per mode.
- **Config additions** — `SPOT_THRESHOLD=4.3`, `SPOT_MAX_SCORE=14.25`, `SPOT_THRESHOLD_MIN/MAX`, `SPOT_THRESHOLD_STATE_FILE`, `FUTURES_CONFIG["max_positions"]=2`, `RISK_CONFIG["max_positions"]` reduced 3→2.

### Changed

- **`generate_signals()`** — new params `mode='futures'` and `threshold_override=None`; futures-only conditions wrapped with `if mode == 'futures':`; HTF condition now key-agnostic (works with `{4h,1d}` or `{1d,1w}`).
- **`fetch_ohlcv_df()`** — added `vwap_period=24` param; spot passes `vwap_period=6` for equivalent 24H VWAP on 4H candles.
- **`display_analysis()`** — accepts `timeframe` and `mode`; header shows mode label; MARKET STRUCTURE hides funding/L/S/OI/basis for spot mode; HTF rows rendered dynamically from dict keys.
- **`_signal_box()`** — selects `SPOT_MAX_SCORE` or `SIGNAL_MAX_SCORE` based on `signal['mode']`.
- **`run_bot.py`** — Phase 2 runs spot then futures. Phase 3 manages positions per mode against separate `max_positions` limits. Phase 4 calls combined `send_signal_alert(spot_signal, futures_signal)`.
- **`paper_trader.py`** — `check_and_close_positions`, `print_open_status`, `print_paper_summary` accept `mode=None` and filter accordingly.

---

## 2026-05-06 — Full-Detail Telegram & Discord Notifications

### Changed

- **Telegram & Discord now send complete analysis** — message matches terminal output with all sections: Trade Setup, Price & Trend, Multi-Timeframe, Technicals, Market Structure, Sentiment + headlines, Signal Reasons, and Paper Performance. Previously only basic entry/SL/TP was sent.
- **`analyze_btc_signal()`** now attaches `_htf`, `_market`, `_news_data`, and `_last` (last candle technicals) to the signal dict before returning. These underscore-prefixed keys are ignored by `log_signal()` but consumed by `notifier.py`.
- **Telegram uses HTML parse mode** (tidak lagi Markdown) agar karakter seperti `&`, `<`, `>`, `-`, `.` di judul berita tidak membreak formatting. HTML di-escape otomatis via `_esc()`.
- **Discord uses code block** untuk trade setup agar angka ter-align dengan font monospace.
- **Message size: ~1200 chars** (well within the 4096-char limit).

---

## 2026-05-06 — Critical Deadlock Fix

### Fixed

- **SQLite deadlock causing bot to freeze** (`signal_history.py`) — `_DB_LOCK` was a non-reentrant `threading.Lock()`. On the first DB access in a new process, `_conn()` acquired the lock then called `_init_tables()`, which called `_conn()` again trying to re-acquire the same lock → deadlock. The bot would hang silently after printing "Computing combined sentiment..." (the PERFORMANCE section in `display_analysis` is the first `_conn()` call when `signal_history.csv` doesn't exist). Fixed by changing to `threading.RLock()` (reentrant lock), which allows the same thread to re-enter without deadlocking while still blocking other threads.

---

## 2026-05-06 — Risk & Signal Quality Fixes

### Fixed

- **Paper P&L blended return** (`paper_trader.py`) — combined P&L was averaging two percentages (`(a+b)/2`) instead of weighting each half equally (`a*0.5 + b*0.5`). These are mathematically equivalent when both exits are exactly 50%, but the old formula implied equal sizing which wasn't the intent. Now uses explicit 50/50 blend for clarity and correctness.
- **StochRSI weight inverted** (`core_analysis.py`) — crossover signals were scored at 0.25 when RSI *confirmed* the same zone, suppressing the strongest signals. Fixed: RSI confirmation now adds a bonus (1.25 vs 1.0 for crossover; 0.6 vs 0.5 for zone-only). Strongest signals now score higher.
- **Cache staleness ignored** (`core_analysis.py`) — stablecoin supply, BTC dominance, and open interest caches were compared against any stored value regardless of age. Added `_cache_fresh()` helper (6-hour TTL): stale previous values are now skipped so trend comparison only uses recent data.
- **Max positions not enforced** (`run_bot.py`) — `RISK_CONFIG["max_positions"]` (3) was set in config but never checked before opening a paper position. Now checked against `get_open_positions()` count before every `open_paper_position()` call.

---

## 2026-05-06 — Signal Confidence Label

### Added

- **Signal confidence label** — `get_signal_confidence(strength, threshold)` returns `STRONG` (≥1.5× threshold), `NORMAL` (≥1.2× threshold), or `WEAK` (≥threshold). Stored as `signal['confidence']` in every non-HOLD signal dict.
- **Terminal display** — `_signal_box()` now shows confidence next to strength: green for STRONG, yellow for NORMAL, dim for WEAK.
- **Notifications** — Telegram and Discord alerts include a `Confidence` line showing STRONG/NORMAL/WEAK.

---

## 2026-05-05 — Sumber Berita & Backtest Slippage

### Added

- **BeInCrypto, CoinDesk, Bitcoinist RSS** — tiga sumber berita gratis ditambahkan ke `news_scraper.py` (`fetch_beincrypto`, `fetch_coindesk`, `fetch_bitcoinist`). Setiap sumber diparsing via XML RSS tanpa API key. Total sumber berita naik dari 3 → 6.
- **Slippage model di backtest** — `backtest.py` sekarang mensimulasikan market impact 0.1% per side (`SLIPPAGE_PCT = 0.001`): BUY entry dibayar lebih mahal, TP dan SL diisi lebih buruk. Membuat hasil backtest lebih mendekati live trading.

### Removed

- **Reddit scraper** (`fetch_reddit_sentiment`) — dihapus karena endpoint JSON unofficial (`reddit.com/*.json`) sering return 429 dan tidak reliable. Digantikan oleh 3 sumber RSS di atas.

---

## 2026-05-05 — Notifikasi Telegram & Discord Diperbarui

### Changed

- **Format pesan Telegram & Discord** — notifikasi BUY/SELL sekarang mencantumkan `Trail SL`, `TP1 (50%)`, dan `TP2 (50%)` sesuai mekanisme partial exit yang baru. TP2 hanya muncul jika tersedia di signal dict. Label `Stop Loss` dan `Take Profit` lama diganti dengan terminologi yang mencerminkan trailing stop dan split exit.

---

## 2026-05-05 — Trailing Stop + Partial Take Profit

### Added

- **Trailing stop loss** — `paper_trader.py` now advances the stop loss every cycle as price moves in our favour. For BUY: `trail = price − ATR × trailing_atr_factor`; for SELL: `trail = price + ATR × trailing_atr_factor`. Trail only moves forward (never against the position). Configured via `RISK_CONFIG["trailing_atr_factor"]` (default `1.0`).
- **Partial take profit (TP1 / TP2)** — positions now exit in two halves:
  - **TP1** (first 50%) = original ATR-based TP. When hit, trailing stop moves to breakeven (entry price), locking in no-loss on the remainder.
  - **TP2** (remaining 50%) = 2× the TP1 distance from entry. Closes when TP2 is hit or trailing stop is triggered.
  - Combined P&L = average of both exits.
- **`signal['atr']`** and **`signal['tp2']`** added to signal dict in `generate_signals()`.
- **`update_trailing_stop(pos_id, new_sl)`** and **`partial_close_position(pos_id, pnl_pct, new_sl)`** added to `signal_history.py`.
- **`paper_positions` schema extended** with: `atr`, `trailing_stop`, `tp1`, `tp2`, `partial_closed`, `partial_pnl`. Existing DBs auto-migrated via `_migrate_paper_positions()`.
- Display (`print_open_status`) now shows `Trail` and `TP2` instead of static `SL` and `TP`, plus `[½ taken]` tag after partial exit.

---

## 2026-05-05 — Bug Fixes Round 2

### Fixed

- **CSV fallback dead code** — `log_signal()` had `return cur.lastrowid` placed before the CSV write block, making the backup CSV never update after the SQLite migration. `return` moved to after the CSV write.
- **RSI divergence index mismatch** — `detect_rsi_divergence()` used `.idxmin()` / `.idxmax()` + `.loc[]` to look up RSI at price extremes. On a datetime-indexed DataFrame, label-based lookup can silently return wrong values on duplicate timestamps. Replaced with `tail['close'].values.argmin()` + `.iloc[]` (position-based), which is always correct regardless of index type.
- **Macro event timezone off by 4–5 hours** — ForexFactory exports timestamps in US Eastern Time (ET), but `check_upcoming_macro_events()` was treating them as UTC after the previous fix (`.replace(tzinfo=UTC)`). Events would be shifted 4–5 hours, causing the 2-hour hedge window to fire at wrong times or miss events. Now parsed as ET then converted: `.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)`.
- **Funding rate missing moderate-positive zone** — Funding rates between +0.01% and +0.05% fell through all conditions and scored as NEUTRAL. Added `VERY NEGATIVE` label for rates below -0.05% and confirmed the symmetric coverage now matches positive and negative zones.
- **SQLite connection not thread-safe** — `_conn()` used a bare global `DB` variable without locking. Added `threading.Lock()` (`_DB_LOCK`) and `check_same_thread=False` to prevent "database is locked" errors when scraper and analyzer run concurrently.

---

## 2026-05-05 — Bug Fixes & Signal Integrity

### Fixed

- **Adaptive threshold non-functional** — `generate_signals()` was comparing against the hardcoded `SIGNAL_THRESHOLD` constant instead of calling `get_adaptive_threshold()`. The adaptive mechanism now actually controls signal firing.
- **Duplicate `log_signal()` definition** — `core_analysis.py` had its own CSV-only `log_signal()` that shadowed the SQLite+CSV version in `signal_history.py`. The local definition was removed; `core_analysis` now imports from `signal_history`, ensuring every signal is written to both SQLite and CSV exactly once.
- **Macro event datetime crash** — `check_upcoming_macro_events()` compared a naive `datetime.strptime()` result against `datetime.now(UTC)` (aware), raising `TypeError`. Fixed by appending `.replace(tzinfo=UTC)` to the parsed timestamp.
- **Paper positions not linked to signals** — `paper_positions.signal_id` was never populated (FK defined but unused). `log_signal()` now returns the SQLite `lastrowid`; `analyze_btc_signal()` stores it as `signal['db_id']`; `open_paper_position()` inserts it into the `signal_id` column. Paper trades can now be correlated to their triggering signal.
- **Win rate diluted by BREAKEVEN** — `get_win_rate()` included BREAKEVEN trades in the denominator, making a 10W/10L/20BE record appear as 25% instead of 50%. Denominator is now `WIN + LOSS` only.
- **Profit factor silent on perfect records** — `get_profit_factor()` returned `None` when there were zero losses, making a flawless record indistinguishable from no data. Now returns `float('inf')`; display shows `∞`.
- **OBV denominator unstable near zero** — OBV slope was normalised against `abs(OBV[-5])`, which becomes erratic when OBV crosses zero. Replaced with `volume[-5:].sum()` — a stable, always-positive reference proportional to recent trading activity.
- **Sentiment double-counting** — `analyze_sentiment()` could award two points for the same signal when a short word token (e.g. `bull`) appeared standalone in text that also contained a longer substring match (`bullish`). Word tokens that are prefixes of an already-matched substring are now excluded from the word-match count.

---

## 2026-05-03 — Major Overhaul

### Added

#### New Modules
- **`signal_history.py`** — SQLite-backed signal and paper position storage. Auto-migrates from legacy CSV on first import. Query helpers: `get_recent_signals()`, `get_win_rate()`, `get_profit_factor()`, `get_closed_pnl()`.
- **`paper_trader.py`** — Auto-close open paper positions when TP or SL is hit. Prints open position status and cumulative P&L summary.
- **`backtest.py`** — 90-day replay of 1H BTC/USDT data through `generate_signals()`. Tracks signal outcomes (WIN/LOSS/OPEN), computes win rate, profit factor, max drawdown. Technical conditions only (market structure data unavailable historically).

#### New Signal Conditions
- **#12 BTC Dominance** — CoinGecko `/global` (free). Rising BTC.D = capital rotating into BTC. Weight: 0.75.
- **#13 Open Interest** — Binance Futures `/openInterest` (free). Rising OI = trend confirmation. Weight: 0.50.
- **#14 Futures Basis** — Mark vs index price spread from `premiumIndex`. Premium = long demand, discount = weak demand. Weight: 0.50. (Replaced broken liquidation heatmap — Binance `/allForceOrders` permanently deprecated.)

#### New Data Sources
- **CoinGecko Trending** — `/search/trending` appended to news CSV. BTC in top trending = retail FOMO signal.
- **Reddit Sentiment** — Scrapes `r/Bitcoin` and `r/CryptoCurrency` hot posts, scores with same keyword engine. Free, no key.

#### Features
- **Discord webhook alerts** — `notifier.py` sends to both Telegram and Discord (if `DISCORD_WEBHOOK_URL` is set).
- **Adaptive threshold** — `SIGNAL_THRESHOLD` auto-raises (+0.50) after >8 signals in 72h, lowers (−0.25) after 0 signals. Bounds: 4.0–8.0. Override with env var (any non-zero value disables adaptation).
- **OBV neutral zone** — Only scores OBV when `|slope| / OBV[-5] ≥ 0.001`. Flat OBV no longer contributes noise.
- **TP capped at S/R** — Take-profit clamped to nearest resistance/support when within 85% of ATR-based TP. Minimum 1.0 R:R maintained.
- **StochRSI weight reduction** — When RSI already in extreme zone (<30 or >70), StochRSI crossover weight drops from 1.0 → 0.25, zone weight from 0.5 → 0.15. Eliminates double-counting of correlated oscillators.

### Changed

#### Code Cleanup (all files)
- **Removed duplicate `_make_session()`** — both `news_scraper.py` and `core_analysis.py` now use shared `HTTP_SESSION` from `config.py`.
- **Fixed `datetime.utcnow()` deprecation** — all occurrences replaced with `datetime.now(UTC)`.
- **Fixed redundant `import os`** — `core_analysis.py:960` (line-level duplicate of top-level import).
- **Renumbered conditions** — `generate_signals()` comments now consistent #1–#17 (was #1–#14 with a misnumbered #12 after #14).
- **Added `load_cache()` / `save_cache()`** helpers to `config.py` for atomic JSON cache writes.
- **Added docstrings** to all public functions.
- **Standardized imports** — alphabetized, grouped stdlib → third-party → local.

#### Sentiment Engine
- **Word-boundary matching** — short/ambiguous tokens (`bull`, `ban`, `rise`, `sell`, `buy`) now matched as whole words, avoiding false positives like `bulldozer`, `urban`, `surprise`.
- **Removed `risk` from negatives** — too contextual ("risk-on" is bullish for BTC).
- Extended positive/negative keyword lists with `soar`, `breakout`, `adoption`, `accumulat`, `exploit`, `lawsuit`, `crackdown`, `downturn`, `liquidat`.

#### Configuration
- `SIGNAL_MAX_SCORE`: 15.0 → 18.0
- `SIGNAL_THRESHOLD`: 4.5 → 5.2 (adaptive)
- `ThreadPoolExecutor max_workers`: 5 → 9
- New env var: `DISCORD_WEBHOOK_URL`
- New cache files: `btc_dom_cache.json`, `oi_cache.json`, `threshold_state.json`
- New SQLite DB: `signal_history.db` (auto-created)

#### Pipeline
- **`run_bot.py`** now has a 4-phase cycle: scrape → analyze → paper trade → notify.
- **`analyze_btc_signal()`** calls `update_threshold_state()` after signal generation.
- **`display_analysis()`** shows BTC Dominance, Open Interest, and Futures Basis sections.

### Removed
- **Liquidation heatmap** (`fetch_liquidation_clusters()`) — Binance `/fapi/v1/allForceOrders` permanently deprecated (400 Bad Request). Replaced with futures basis signal that uses existing `premiumIndex` data.

### Fixed
- **`data/` directory not found** — `os.makedirs(DATA_DIR, exist_ok=True)` added to `config.py` on import, so scraping/analysis work even if `data/` was deleted.
