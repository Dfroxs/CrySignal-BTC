# Results: H-MC (the macro-event rule) and H-FG (the Fear & Greed overlay)

Pre-registration: `2026-11-19-macro-fng-prereg.md`, committed at `ac3b7c0` before either test
was run. Instruments: `scripts/macro_rule_ic.py` and `scripts/fng_ic.py`. Raw output is in
`2026-11-19-macro-fng-run/`.

| test | verdict | figure |
|---|---|---|
| H-MC: macro rule (hold and force-close 0–2 h before a High USD event) | **INCONCLUSIVE** | mean +0.221 pp per affected signal, 90% CI [−0.066, +0.500], 268 signals / 243 episodes |
| H-FG1: FEAR (F&G ≤ 20) excess > 0 | **INCONCLUSIVE** | +0.197 pp, CI [−0.600, +1.064], halves +0.056 / +0.405, 424 days |
| H-FG2: GREED (F&G ≥ 70) excess < 0 | **FAIL** | **+0.687 pp**, CI [+0.066, +1.324], halves +1.335 / +0.104, 666 days |

## H-MC

- **Events:** 4,545 timed USD High events. 1,365 were excluded because their time varies
  (speeches, testimonies, elections). 1,004 fall in the window.
- **Signals:** 521 BTC futures signals (Arm C recording, the run-4 engine), from 2022-01-01
  to 2025-03-28, none unresolved.
- **The rule acts on 51% of them:** 37 were suppressed (the signal sat inside a window) and
  231 were force-closed.
- **Effect:** mean d = pnl_rule − pnl_base = **+0.221 pp**, and the CI includes 0. Leaning
  helpful, but not shown.
- **Reported, never criteria:**
  - suppressed: +0.428 pp (n 37);
  - force-closed: +0.188 pp (n 231);
  - by family: jobs +0.016 (90), inflation +0.457 (44), FOMC −0.152 (19), other +0.353
    (115).

**Licensed:** nothing changes. The rule stays (HELPS or INCONCLUSIVE). Two facts are new and
worth keeping:
- the rule touches half of all futures signals, far more than the 3 live MACRO_CLOSEs suggest;
- nothing in this sample says it hurts.

## H-FG

- **Data:** 3,206 days in the window (2018-02-01 to 2026-11-15), from the alternative.me
  daily index and BTC/USDT daily closes.
- **Forward return:** D's open to D+2's close (72 h).

**H-FG2 FAIL: greed comes before *better* returns, not worse.** On days with F&G ≥ 70,
BTC's 72 h return beat the all-days mean by +0.69 pp, with the whole CI above 0 and both
halves positive. Extreme greed (≥ 80, 165 days) is +2.20 pp (descriptive). The overlay's
−0.5 strength penalty on a BUY when F&G ≥ 70 bets the wrong way. It can drop a BUY just over
the bar to HOLD in exactly the regime that kept rising.

**H-FG1 INCONCLUSIVE:** extreme fear's excess is +0.20 pp with a CI across 0. FEAR30 (≤ 30) is
−0.22 pp (descriptive). There is no support for the +1.5 BUY bonus at F&G ≤ 20, nor for the
−0.5 SELL penalty at ≤ 30.

**Descriptive:** the Spearman IC of F&G against the forward return is +0.039 (72 h), +0.025
(24 h) and +0.039 (7 d). If anything it is weak momentum, not the contrarian signal the
overlay assumes.

**Licensed:**
- Remove the −0.5 BUY penalty at F&G ≥ 70 (run 4 part B).
- The FEAR leg (the +1.5 BUY bonus at ≤ 20 and the −0.5 SELL penalty at ≤ 30) is unsupported.
  It is listed as a candidate to neutralise, an owner decision, and stays meanwhile.

## No profit claim

These decide whether two live behaviours earn their place, not whether the bot has an edge.
