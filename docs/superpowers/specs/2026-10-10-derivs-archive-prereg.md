# Pre-registration — do the 30-day derivatives stats carry information? (H-D)

**Written 2026-10-10, before the archive has run in production and before any figure
below has been computed.** A test fetch on the VPS (BTC, DOGE) checked only row counts and
column names. No IC, return or correlation was computed from it, and its files were deleted.

## Why

Every engine component tested so far has failed to show edge outside its own period
(STEP 1), and costs and gates are not what makes futures lose (H-LO, H-RG). The one lead
that has not been tested out of sample is the live-only data. In the exploratory 40-day
scan, BTC **global L/S ratio** had IC +0.15/+0.26 at 12/24h, the opposite of the engine's
contrarian reading. H-L2 tests that on run-3 `cycle_log`, BTC only, about 700 hours.

Binance keeps L/S, open-interest and taker history for 30 days only, so it cannot be
backtested like basis and funding (H-F). `scripts/archive_binance_derivs.py` keeps it,
hourly, for BTC, ETH, SOL, BNB, XRP and DOGE. Its first run backfills 29 days. H-D uses
the archive to test the L/S lead **across markets**, which H-L cannot, and to screen two
fields that have never been looked at.

## Data (fixed now)

- `data/derivs/<SYMBOL>_<stat>.csv` on the VPS, written only by the archive script.
  Stats: `ls_global` (`longShortRatio`), `ls_top_position` (`longShortRatio`), `oi`
  (`sumOpenInterest`), `ls_top_account`, `taker` (`buySellRatio`).
- **Forward return:** Binance USDⓈ-M perp 1h klines, fetched at scoring time. A stat
  stamped T is treated as known at **T + 1h**, a conservative rule because the taker
  stamp is the bar's open. r runs from the open of the kline starting at T + 1h to the
  close of the kline starting at T + 24h: a 24h horizon that begins only after the stat
  is known.
- **Alts (ETH, SOL, BNB, XRP, DOGE):** every archived hour from the first backfilled row
  (~2026-09-10) to rows whose forward return completes by **2026-11-08 00:00 UTC**. None
  of these symbols' derivative stats were ever examined.
- **BTC:** only rows stamped **≥ 2026-10-09T12:34:20Z** (run 3's start). Earlier BTC
  rows overlap the exploratory scan and are in-design, so they are never scored.

## Statistics

Spearman IC per symbol between the stat (level, or for `oi` the 24h % change,
`oi[T]/oi[T−24h] − 1`) and r. Pooled IC = mean of the per-symbol ICs. 90% interval:
**block bootstrap, 24h blocks resampled jointly across symbols** (the same calendar blocks
for every symbol), 2,000 resamples, seed 2026. Power guard: fewer than 500 hours with
stat and return present makes that symbol not counted. Fewer than 4 alts counted makes
the hypothesis INCONCLUSIVE.

## Hypotheses (alts pooled)

**H-D1, global L/S is a momentum signal (direction fixed by the lead: IC > 0).**
- **PASS** if pooled IC > 0, the interval lies entirely above 0, **and** ≥ 4 of 5 alts
  have IC > 0.
- **FAIL** if the interval lies entirely below 0, or pooled IC ≤ 0.
- **INCONCLUSIVE** otherwise.

**H-D2 top-trader position L/S** and **H-D3 OI 24h change** have no prior direction. They
are a screen, not a test. A field is a **lead** if the pooled interval excludes 0 **and**
≥ 4 of 5 alts share its sign. A lead fixes a direction for a later, separate
pre-registration. It licenses nothing by itself. Two fields are screened, so one lead
at 90% is weak evidence.

## Reported regardless, never criteria

Per-symbol ICs, `ls_top_account` and `taker` ICs, the BTC run-3-window ICs for every
stat (descriptive; H-L2 owns the formal BTC test), ICs at 4h and 12h, the archive's gap
log, and the rows per symbol.

## What the outcomes license

- **H-D1 PASS and H-L2 PASS:** an L/S momentum condition becomes the first run-4 entry
  candidate with out-of-sample support. It still needs its own condition-vs-random
  test before it ships.
- **H-D1 PASS, H-L2 not PASS:** the cross-market evidence stands, BTC does not confirm,
  and the archive keeps running for a second window.
- **H-D1 FAIL:** the L/S lead is dropped. The live-only data has not rescued the entry,
  and run 4's direction has to change (a different strategy family), not its weights.

## Discipline

- No IC or return figure from the archive is computed before **2026-11-08**. Health checks
  (row counts, gaps, the cron log) are allowed.
- The scoring script is written and committed before it is run, like every instrument
  here.
- The archive script's symbols, stats and file format are frozen until scoring. Adding
  symbols is allowed, but they are not scored in H-D.

## Discard

- Any row whose forward return uses a price before T + 1h.
- Any BTC row before 2026-10-09T12:34:20Z in a scored set.
