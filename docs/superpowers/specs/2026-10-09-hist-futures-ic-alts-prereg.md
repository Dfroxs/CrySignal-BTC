# Pre-registration — H-F2/H-F3: does the BTC basis/funding effect hold in other markets, and has it decayed?

**Written 2026-10-09, after H-F (BTC) was scored and before any non-BTC data below has
been fetched.** No figure for these symbols exists in this repository.

## Why

H-F passed basis and funding on BTC (high z168 = weaker next 24h). But both faded or
flipped in 2025–2026, the years that matter for a run-4 condition. BTC's own fresh data
(from 2026-10-10) is too short to separate an IC of 0.05 from zero for months. Two
questions can be answered now on data no experiment here has read:

- **H-F2 (market change):** is the effect real outside BTC? STEP 1's standard is
  survival under a change of market.
- **H-F3 (decay):** was 2025–2026 a BTC-only wobble, or did the effect leave the whole
  market?

## Data (fixed now)

Binance USDⓈ-M perpetuals **ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT**. 1h,
from 2020-01-01 (or the first listed hour) to **2026-08-29 23:00 UTC**. Fields, transform
(trailing 168h z-score, ≥ 120 valid hours), forward return (24h log) and the fetcher
are identical to H-F (`scripts/fetch_futures_history.py`, `scripts/hist_futures_ic.py`),
apart from the symbol. The CSVs go in `2026-10-09-hist-futures-ic-alts-run/<SYMBOL>/`
with SHA-256 recorded.

The sign is **fixed in advance from BTC: negative** for both fields.

## H-F2 — the effect holds in other markets (full window)

For each field ∈ {basis_pct, funding_rate}, compute the per-symbol Spearman IC of z168
against the forward 24h return, with a 95% block bootstrap (168h blocks, 2,000
resamples, seed 2026).

- **PASS** if the IC is negative **and** its CI lies entirely below 0 in **≥ 4 of 5**
  symbols.
- **FAIL** if that holds in **≤ 1 of 5**, or if any symbol's CI lies entirely **above** 0.
- **INCONCLUSIVE** otherwise.
- Power guard per symbol: < 20,000 valid hours → that symbol counts as not supporting.

## H-F3 — the effect survives 2025–2026

The same per-symbol IC restricted to **2025-01-01 → 2026-08-29**. The block bootstrap is
as above, but the interval is **90%**, since this is the decay check on a shorter span.

- **PASS (no decay)** if the point IC is negative in **≥ 4 of 5** symbols **and** the
  CI lies entirely below 0 in **≥ 2**.
- **FAIL (decayed)** if the point IC is negative in **≤ 2 of 5** symbols.
- **INCONCLUSIVE** otherwise.
- Power guard: < 8,000 valid hours in the span → that symbol counts as not supporting.

## Reported regardless, never criteria

Per-year ICs per symbol, taker_ratio per symbol, and quintile tables.

## What the outcomes license

- **H-F2 PASS and H-F3 PASS:** write the run-4 condition pre-registration:
  z168 {funding −1, basis −1}, one combined condition (they correlate at 0.37), tested
  against a count-matched random baseline on post-2026-10-09 data.
- **H-F2 PASS, H-F3 FAIL:** the effect was real and has been arbitraged away. Record it,
  and do not build the condition. Fix only the engine's backwards basis sign (a
  correctness change, not an edge claim).
- **H-F2 FAIL:** BTC's pass was one market's coincidence, the same pattern STEP 1 kept
  finding. Same conclusion as above.
