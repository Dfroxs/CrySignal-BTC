# Pre-registration — do basis, funding and taker flow predict BTC? (historical, H-F)

**Written 2026-10-09, before any of the data below has been fetched.** No figure from
this window exists in this repository.

## Why

`backtest.py` scores funding, L/S, OI, basis and taker flow as NEUTRAL because "no free
historical API serves them". That holds for L/S and OI: Binance keeps 30 days of them.
**It does not hold for the other three.** Binance serves years of 1h mark-price and
index-price klines (so the basis can be rebuilt with the live formula), the settled
funding history, and per-kline taker-buy volume. Those three fields can therefore be
tested on six years of data that no experiment here has read, instead of waiting for
run 3.

This does not touch run 3. H-L (basis, L/S on run-3 live cycles) stays as registered and
is scored on day 30. This test uses no data from 2026-08-30 onward, the span of the
exploratory 40-day scan, runs 1–3 and H-B.

## Data (fixed now)

- Binance USDⓈ-M perpetual **BTCUSDT**, 1h, **2020-01-01 00:00 → 2026-08-29 23:00 UTC**.
- `basis_pct` = (mark close − index close) / index close × 100. This is
  `signals/market_data.fetch_funding_rate`'s formula, applied to hourly
  `markPriceKlines` and `indexPriceKlines`.
- `funding_rate`: settled rates from `/fapi/v1/fundingRate`, each held forward until the
  next settlement. Live reads `lastFundingRate`, the current period's running estimate,
  so this is a lagged proxy, and the results will say so.
- `taker_ratio` = taker-buy volume / (volume − taker-buy volume) per 1h perpetual
  kline. This is the quantity `takerlongshortRatio` reports, rebuilt from the kline.
- Forward return: log(close[t+24] / close[t]) on the perpetual's own 1h close. Each
  field is read at the close of hour t, so it is known when the bot would act.
- Raw downloads are saved as CSV in `docs/superpowers/specs/2026-10-09-hist-futures-ic-run/`
  with SHA-256 recorded. Gaps stay as gaps and are never filled across.

## Transform (fixed now)

The primary form of each field is its **trailing 168h z-score**: (x − mean) / std over
the previous 168 hours, excluding hour t's own value from neither side of the fit and
requiring 120 valid hours. This is the relative-band idea `signals/variants.py` already
uses. It removes the multi-year drift in funding and basis levels that would otherwise
dominate a rank correlation. The raw level is reported, but descriptive only.

## Hypotheses

For each field f ∈ {basis_pct, funding_rate, taker_ratio}, compute the Spearman IC between
z(f) at hour t and the forward 24h return.

**H-F (one per field).** The field carries information about the next 24h.

- Interval: **95%** moving-block bootstrap. The blocks are 168h, because forward 24h
  returns overlap and the z-score window is 168h. 2,000 resamples, seed 2026. 95% rather
  than the project's usual 90% because there are three fields.
- **PASS** if both hold:
  1. the 95% CI excludes 0, and
  2. the per-year IC has the same sign as the pooled IC in **≥ 5 of the 7** calendar
     years (2020–2025, plus 2026 through Aug 29).
- **FAIL** if the CI contains 0 **or** fewer than 4 of 7 years agree in sign.
- **INCONCLUSIVE** otherwise (CI excludes 0, but only 4 of 7 years agree).
- Power guard: fewer than 20,000 hours with the field, its z-score and the forward return
  all present → INCONCLUSIVE.

The direction is not fixed in advance. The engine reads funding contrarian (high =
bearish), taker flow as momentum (high = bullish) and basis as momentum (high =
bullish). The results state whether a passing sign agrees with the engine's reading. A
pass against the engine's sign is still a pass. It means the condition is wired
backwards.

## Reported regardless, never criteria

- ICs at 4h and 12h horizons, and of the raw (un-z-scored) level.
- Quintile table: mean forward 24h return per z(f) quintile, to show whether an IC that
  passes is monotone or lives in one tail.
- The correlation between the three z-scores, so overlapping information is visible.

## What a PASS licenses, and what it does not

- A PASS licenses **designing** a futures condition on that field's z-score (the sign
  as measured). That condition then needs its own pre-registered test against a
  count-matched random baseline before it may enter `config.py`. An IC is not a
  strategy, and costs are not in it.
- A FAIL on all three means the live-only fields the backtest cannot see were not
  where the missing edge was, at least for these three. The search then moves to
  [L/S and OI via run-3 H-L] or a different strategy family.

## What discards the run

- Mark/index klines missing for more than 5% of the window's hours.
- Any field computed with data from after hour t (a look-ahead found in review).
