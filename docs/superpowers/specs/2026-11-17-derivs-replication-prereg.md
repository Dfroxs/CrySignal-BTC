# Pre-registration: global L/S as momentum, second window (H-DR)

**Written 2026-11-17, before any IC on archive rows stamped on or after 2026-11-08 has been
computed.** H-D (`2026-10-10-derivs-archive-prereg.md`) scored rows whose 24h return completed
by 2026-11-08 00:00 UTC. `hd_eval.py` loaded later klines on 2026-11-15 but scored and printed
nothing past that cutoff. The scanner's hourly context also holds `ls_ratio` per coin, but no
correlation has been computed from it.

## Why

H-D asked whether Binance's global long/short account ratio is a momentum signal (high L/S
predicts a higher forward 24h return). Result on 2026-11-15:

- Pooled alt IC **+0.047, 90% CI [-0.060, +0.165]**: INCONCLUSIVE.
- BTC alone (run-3 window) **+0.282**: a lead, not a criterion.
- No other stat produced a lead.

INCONCLUSIVE with a positive point estimate is the case where a second window is worth
collecting. Binance keeps these stats for 30 days only, so the archive is the only way to
get one. Nothing else in this project's history has shown out-of-sample promise on live-only
data.

## Data (fixed now)

- `data/derivs/<SYMBOL>_ls_global.csv` (`longShortRatio`), written only by
  `scripts/archive_binance_derivs.py`. Forward return and timing are exactly as in H-D:
  - a stat stamped T is known at T + 1h
  - r runs from the open of the kline starting at T + 1h to the close of the kline starting
    at T + 24h
- **Window:** stamps from **2026-11-08 00:00 UTC** whose 24h return completes by
  **2027-02-06 00:00 UTC** (90 days). This applies to every symbol. No H-D row overlaps it.
- **Alts (8):** ETH, SOL, BNB, XRP, DOGE (H-D's five), plus ADA, LINK and AVAX, which join
  the archive today. Their first run backfills 29 days, so they cover the window from its
  start.
- **BTC:** scored alone, same window.

## Statistics

Unchanged from H-D (`hd_eval.pooled_ic`):

- Spearman IC per symbol; pooled IC = the mean over symbols counted.
- 90% CI from moving 24h blocks on one calendar shared by every symbol; 2,000 resamples,
  seed 2026.
- A symbol counts only with 500 or more hours that have both a stat and a return.

## Hypotheses

**H-DR1 (alts pooled, ls_global, IC > 0):**
- **PASS:** pooled IC > 0, CI lower bound > 0, and at least 6 of 8 alts have IC > 0.
- **FAIL:** pooled IC ≤ 0, or the CI lies entirely below 0.
- **INCONCLUSIVE:** otherwise. Also INCONCLUSIVE when fewer than 6 alts are counted.

**H-DR2 (BTC alone, ls_global, IC > 0):**
- **PASS:** IC > 0 and CI lower bound > 0.
- **FAIL:** IC ≤ 0.
- **INCONCLUSIVE:** otherwise.

**Power, stated now.** H-D's CI was 0.225 wide over ~58 days and 5 alts. With 90 days and 8
correlated alts, expect roughly 0.17–0.19. At that width, H-D's own point estimate (+0.047)
would still be INCONCLUSIVE. PASS needs a true pooled IC of about +0.09 or more. If the
result is INCONCLUSIVE again, that is the expected outcome for a weak or absent effect. It
is not a reason to extend the window.

## Reported regardless, never criteria

- per-symbol ICs
- the other four stats' pooled ICs
- 4h and 12h ICs
- archive gaps and rows per symbol

## What the outcomes license

- **H-DR1 PASS and H-DR2 PASS:** an L/S momentum entry condition becomes a run-4 candidate.
  It still needs its own condition-vs-random test before it ships.
- **H-DR1 PASS only:** the cross-market effect stands, BTC does not confirm, and nothing
  ships for the BTC bot. It may be used as a scanner input in a later, separate prereg.
- **H-DR1 FAIL:** the L/S momentum lead is dropped for good.
- **INCONCLUSIVE:** the lead is dropped as too weak to act on at this data size. The archive
  keeps running; it costs nothing.

## Discipline

- Instrument: `scripts/hdr_eval.py`, committed with this file, before any data in the
  window is scored. It refuses to score before 2027-02-06.
- No IC, correlation or return figure is computed from window rows before then. Health
  checks are allowed: row counts, gaps, the archive log, and the alarm's freshness check.
- The archive's symbols, stats and file format are frozen until scoring.
- Scored once. It is never re-scored with a longer window to rescue a result.
