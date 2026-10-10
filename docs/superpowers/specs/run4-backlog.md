# Run 4 backlog

What run 4 should change, and why. Nothing here ships during run 3: every change to
`signals/`, `config.py`, `trading/paper.py` or Phase 3 restarts run 3's window
(see `2026-10-09-run3-prereg.md`). Each item cites its evidence and says whether it is a
**correctness fix**, a **tested change** or a **design decision** (data yield, no profit
claim). Started 2026-10-09, updated 2026-10-10.

## Correctness fixes (no edge claim needed)

| item | evidence | change |
|---|---|---|
| Basis condition reads backwards | H-F: BTC z168 IC −0.048 (high basis → weaker 24h). H-F2: negative in 5/6 markets, interval below 0 in 4, above 0 in none (`2026-10-09-hist-futures-ic-*-results.md`) | **Neutralise** basis (score 0). Flipping it needs the condition test that has not passed |
| Futures taker fee likely 0.05%, not 0.04% | Binance regular-tier USDⓈ-M taker fee | `EXECUTION_CONFIG["futures_fee_pct"]` 0.04 → 0.05 after checking the account's real tier. Paper P&L and backtests are currently slightly optimistic |
| FLIP and BREAKER_CLOSE send no Telegram close message | Telegram-format audit 2026-10-10: both close via `close_paper_position` in run_bot.py Phase 3 and never reach `all_closed`, so the owner never hears of them | route both closes into the Phase 4 close notification. Phase 3 code, so run 4 only |
| `psy_sl_first` uses a fixed $1,000 step | H-PSY prereg 2026-10-10: only "round" for BTC between $10k and $100k; above $100k the real round levels are $10k/$5k | step from price scale, as H-PSY's instrument does for non-BTC. Equal to today's rule at today's price |
| `taker_ratio` condition carries nothing | H-F: IC +0.002, FAIL. H-F2: no symbol passes | Prune: add `"taker"` to `DISABLED_CONDITIONS` (ceiling 1.00 futures; the threshold scales with it) |

## Tested changes (need their own pre-registered PASS first)

| item | status | next step |
|---|---|---|
| Limit-order entries (maker fee, no entry slippage) | **H-LO INCONCLUSIVE** (2026-10-10): d̄ +0.014pp [−0.041, +0.059] primary, +0.089pp confirmation. The 4% of trades the limit misses are the runaway winners (+1.44pp) | **not licensed**. Even the best case (+0.09pp) leaves futures negative; revisit only if an entry ever shows edge |
| L/S momentum condition (global L/S, IC > 0) | H-D pre-registered 2026-10-10 on the derivatives archive (5 alts) plus H-L2 (BTC, run 3) | score both on 2026-11-08; a candidate only if both PASS |
| z-score funding/basis condition {funding −1, basis −1} | H-F PASS on BTC, H-F2/H-F3 INCONCLUSIVE, so **not licensed** | only if run-3 live data (H-L) or a later window confirms it |

## Design decisions already shipped in run 3 (re-examine at day 30)

| item | shipped | question for day 30 |
|---|---|---|
| Futures opens from WEAK (dead zone) | 2026-10-09 10:55 | did WEAK-band futures trades lose more than NORMAL ones? |
| Spot anti-chase vetoes off | 2026-10-09 12:34 | did the extra spot entries behave like the 09-24 test said (no worse)? |
| R:R gate tolerance (fix) | 2026-10-09 12:34 | n/a (correctness) |

## Gates and vetoes: tested status

| gate | result | status |
|---|---|---|
| anti-chase vetoes (spot) | 09-24 H-B FAILED to show they pay | off for spot since run 3 |
| counter-trend veto | H-CT INCONCLUSIVE, but vetoed shorts are worse than random in both windows | **stays** |
| futures Phase 3 gates | 10-09 INCONCLUSIVE (overlap) | stay |
| spot regime gate | H-RG: primary PASS (blocked −1.37pp vs kept −0.36pp, diff +1.0pp, CI > 0); confirmation n=4, so INCONCLUSIVE overall | **stays** — the first gate with a measured selection effect |
| spot regime gate, live | H-RGL pre-registered 2026-10-10, scored from 2026-11-11 | stays until then |
| spot `sr_first` | H-SR 2026-10-10 INCONCLUSIVE: primary kept − only +0.26pp [−0.53, +1.08]; confirmation flips (n=12) | **design-decision candidate**: removing it adds ~50% spot entries (BTC: ~+1–1.5 trades/month), cost indistinguishable from 0. Not worth restarting run 3 for |
| spot `psy_sl_first` | H-PSY 2026-10-10 INCONCLUSIVE, blocks few (35 vs 347 kept), blocked BUYs no better than random | stays |
| short-term (5-SMA) veto | untested | candidate |

## Not to re-propose (already failed)

- Exit tweaks on spot (hold cap, looser post-TP1 trail, no TP1 partial): 09-21, all FAILED.
- Retuning weights or thresholds: STEP 1, so any value is fitted to noise.
- Trading the altcoin set with this engine: loss diagnosis, alts lose.
