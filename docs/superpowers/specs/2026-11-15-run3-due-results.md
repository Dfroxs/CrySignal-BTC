# Results: the two run-3 hypotheses that were due (H-RGL, H-D)

Scored 2026-11-15 12:50 to 13:20 UTC, from a fresh `.backup` of the VPS database (H-RGL)
and a copy of the derivatives archive (H-D). Both instruments ran as committed; neither
was edited. H-V1, H-V2 futures, H-S and H-X are **not** scored here: their day 30 is
2026-11-16 (`futures_restart_at` 2026-10-17 14:39:46 UTC; `score_run3.py` gates on the
normalised date).

Raw output: `2026-11-15-regime-live-run/result.json`, `2026-11-15-derivs-run/hd.txt`,
`hd_result.json`. The perp 1h klines H-D reads were fetched on the VPS with
`hd_eval.py --fetch-klines` (as its docstring directs, at `nice 19`, download only) into
`data/derivs/klines/` there, then copied; scoring ran locally.

## H-RGL (`2026-10-10-regime-live-prereg.md`): INCONCLUSIVE

Are the spot BUYs the live `regime_bearish` gate blocked worse than random entries in
the same regime?

| | n | mean per trade (live spot exit) |
|---|---|---|
| blocked by the regime gate | **6** | **-1.066pp**, 90% CI [-1.789, -0.154] |
| count-matched random, bearish-regime bars | 120 (80-bar pool) | -1.060pp |

Unresolved: 0. The blocked entries and random bearish-regime entries lost the same amount
(-1.07 vs -1.06pp), so the gate neither demonstrably saves nor costs anything on this
window, and six trades cannot say more. Reported, not criteria: of 101 bearish-pool bars,
80 were unblocked; the gate combinations most often co-firing with the regime block were
`regime_counter + trend_confluence` (17 bars).

## H-D (`2026-10-10-derivs-archive-prereg.md`): INCONCLUSIVE

Do Binance's 30-day derivatives stats predict the forward 24h perp return? Archive: 1,375
hourly rows per alt, 0 gaps; BTC from run 3's start 683 rows, 0 gaps.

| stat | role | pooled IC over the 5 alts, 90% CI | verdict |
|---|---|---|---|
| `ls_global` | **H-D1** | +0.047 [-0.060, +0.165] | **INCONCLUSIVE** |
| `ls_top_position` | screen | -0.029 [-0.117, +0.066] | no lead |
| `oi_change_24h` | screen | +0.023 [-0.064, +0.107] | no lead |
| `ls_top_account` | descriptive | +0.043 [-0.062, +0.164] | |
| `taker` | descriptive | -0.008 [-0.039, +0.019] | |

Every interval contains zero. Per alt, `ls_global` ranges from +0.222 (BNB) to -0.094
(DOGE): the sign is not stable across assets. BTC alone, reported separately and not a
criterion, is higher (+0.282 for `ls_global`, n = 683), with every BTC figure drawn from
the one asset whose run-3 data are also the bot's live window.

## What is licensed

Nothing changes. H-RGL: the spot regime gate stays as it is (it was kept earlier on
other evidence, `counter-trend-veto` / H-RG). H-D: no derivatives condition is added to the
engine from this result.

The BTC-only L/S figure is a lead for the futures window scored on 2026-11-16 (H-L2
reads `ls_ratio` against the bot's own cycles), nothing more. The archive keeps
accumulating; a future test needs a new pre-registration on data not yet read.
