# Results — do basis, funding and taker flow predict BTC? (H-F)

Scored against `2026-10-09-hist-futures-ic-prereg.md`, committed (`4271d44`, clarified
in `247af84`) before any data was fetched. The scorer `scripts/hist_futures_ic.py` was
committed (`057cb8f`) before it was run. Raw output is in `2026-10-09-hist-futures-ic-run/`
(`run.txt`, `result.json`, `sha256.txt`). The CSVs are not committed (15 MB).
`scripts/fetch_futures_history.py` re-downloads them, and the hashes verify the copy.

**Data:** Binance BTCUSDT perpetual, 1h, 2020-01-01 → 2026-08-29 23:00, 58,392 hours.
Mark, index and perp klines are each 0.00% missing, so no discard.

## Verdicts

| field | IC (z168 vs fwd 24h) | 95% block CI | years agree | verdict | vs engine |
|---|---:|---|---:|---|---|
| basis_pct | **−0.048** | [−0.072, −0.026] | 5/7 | **PASS** | **opposes** (engine reads high basis as bullish) |
| funding_rate | **−0.056** | [−0.085, −0.030] | 6/7 | **PASS** | agrees (contrarian) |
| taker_ratio | +0.002 | [−0.007, +0.010] | 4/7 | **FAIL** | n/a |

Per year:

| field | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (→ Aug) |
|---|---:|---:|---:|---:|---:|---:|---:|
| basis_pct | −0.070 | −0.053 | −0.085 | −0.061 | −0.058 | **+0.019** | **+0.007** |
| funding_rate | −0.078 | −0.057 | −0.074 | −0.069 | −0.078 | **+0.034** | −0.016 |
| taker_ratio | +0.024 | +0.012 | +0.005 | −0.013 | −0.002 | −0.019 | +0.000 |

## What it says

1. **A premium that is high relative to its own past week is followed by weaker 24h
   returns.** That applies to both the perp basis and funding. When leveraged longs pay
   more than usual, BTC tends to lag over the next day. Top versus bottom basis quintile:
   −0.10% against +0.31% per 24h.
2. **The engine reads basis backwards.** It scores basis > +0.10% as BULLISH. Six years
   of data say a relatively high basis is the bearish reading. The engine's funding
   direction (contrarian) is right, but its absolute bands (±0.01%/±0.05%) almost never
   fire in a way that tracks this. Run 2 scored funding 0 of 92 cycles.
3. **Taker flow carries nothing at 24h.** Its condition is noise, as wired and in any
   direction.
4. **basis and funding overlap** (z-score Spearman 0.37). They are partly the same
   signal, so stacking both as separate full-weight conditions would double-count it.

## Why this is not yet an edge, written before anyone acts on it

- **The effect weakened or flipped in the most recent years.** It was negative in every
  year 2020–2024, then +0.019 / +0.007 for basis and +0.034 / −0.016 for funding in
  2025–2026. The pass criterion (≥ 5 of 7) holds, but the most recent data is exactly
  where it fails. This is the pattern STEP 1 found for every TA condition, and a
  crowded, well-known signal decaying is the textbook explanation. **The current regime
  may have no edge here at all.**
- **The size is small.** |IC| ≈ 0.05 on hourly-sampled 24h returns. The quintile spread
  (~0.4pp per 24h, top versus bottom) is about the size of one round trip's cost
  (0.30pp). It is not a stand-alone strategy.
- **It contradicts the exploratory live scan.** That scan (run 1+2, 40 days) found basis
  IC **positive**. 40 days inside 2026 is consistent with the 2025–2026 flip above, and
  it is why H-L (run 3) stays as registered. H-L now doubles as a test of whether the
  flip persists.
- Funding here is the settled rate held forward, a lagged proxy for the live running
  estimate.

## What the PASS licenses (and only this)

Per the pre-registration: **designing** a futures condition on z168(basis) and/or
z168(funding) with the **measured sign (high = bearish)**. That condition must then pass
its own pre-registered test against a count-matched random baseline. Because the
2025–2026 sign is exactly what is in doubt, that test must be scored on data from after
2026-08-29, which means run-3 live cycles or a later window. The 2025–2026 years here
have now been looked at.

Concretely, for the next pre-registration (not done here):
- Fix the engine's basis direction or drop the condition. As wired, it scores the
  bearish case as bullish.
- Replace the absolute funding/basis bands with the z-score form. `signals/variants.py`
  already logs z-score variants live, but **neither uses this sign set**:
  `rel_engine_dir` = {funding −1, basis +1} and `rel_ic_dir` = {funding +1, basis +1}.
  The direction H-F measured, {funding −1, basis −1}, is a third variant. H-V1 at day 30
  cannot speak for it. It can still be scored on run-3 cycles offline, from the logged
  `funding_rate` and `basis_pct` columns, once day 30 allows.
- Drop or zero `taker_ratio`.

None of this changes `config.py` or `signals/` now. Run 3 is pinned, and these are
inputs to run 4's design.
