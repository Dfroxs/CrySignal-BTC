# Pre-registration: run-4 part B candidates on run 4's own cycles (H-VR, H-BS)

**Written 2026-11-18, before the window below holds a single scored cycle.** Run 4 started
2026-11-17 18:44:50 UTC (`3377027`). At writing, the window holds about a day of cycles.
No IC, return or variant figure has been computed on any of them.

## Why

Run 4's plan (`PAPER_RUN.md`) leaves two engine changes waiting on evidence:

1. **`rel_ic_dir`** (`signals/variants.py`): funding and L/S read against their own trailing
   week, in the momentum direction. H-V1 (run 3) passed, Δ IC vs base +0.126 [+0.046,
   +0.211]. Its own IC was +0.023 [-0.120, +0.150], indistinguishable from zero. It passed
   by being less wrong than a base that sat below zero over one month and one regime. A
   PASS here must therefore show both things.
2. **Basis direction.** The basis condition has been **disabled since 2026-10-15**
   (`DISABLED_CONDITIONS`, run-3 amendment) because the evidence conflicts:
   - H-F (2020-01 to 2026-08, z168): high basis is bearish, IC -0.048, fading in 2025-26.
   - H-L1 (run 3 live, raw level): high basis is bullish, +0.170 [+0.051, +0.282].

   The question is which way, if either, holds on hours neither test saw. The engine
   review of 2026-11-18 called basis "read backwards"; that was stale, since the condition
   does not score live.

## Data and window (fixed now)

- `cycle_log` futures cycles of run 4 in `data/signal_history.db`, read by `live_ic.load_cycles`.
  - Placeholders become NaN.
  - The net score per book comes from `cycle_log.variants`.
  - The forward 24h return comes from the cycle price.
- **Window:** cycles from the first full hour after 2026-11-17 18:44:50 UTC whose 24h return
  completes by **2027-01-16 18:44:50 UTC** (60 days). That is also the earliest scoring date.
  Neither H-V1 (to 2026-11-16) nor H-L1 saw any of these hours.
- **Statistics as `live_ic`:**
  - Spearman IC against the forward 24h return.
  - Moving 24h blocks, 2,000 resamples, seed 2026, 90% interval.
  - For H-VR, the paired difference uses the same resampled rows for both books.
- **Power guard:** 1,000 cycles with every input present.
- **Health:** the window is discarded (INCONCLUSIVE) if more than 5% of cycles lack
  variants or more than 10% have a funding placeholder (`live_ic.health`).

## H-VR: does `rel_ic_dir` replicate, and predict on its own?

- **PASS:** both of these hold.
  - Δ = IC(net `rel_ic_dir`) − IC(net `base`) > 0, with the 90% CI above 0.
  - IC(net `rel_ic_dir`) > 0, with the 90% CI above 0.
- **FAIL:** Δ ≤ 0.
- **INCONCLUSIVE:** otherwise, including "beats base but does not predict on its own".

Note: basis is disabled in every book, so this tests the funding and L/S relative bands.

## H-BS: which way does basis point? (two-sided)

IC of raw `basis_pct` (the H-L1 form) against the forward 24h return.

- **BULLISH:** the 90% CI lies entirely above 0.
- **BEARISH:** the 90% CI lies entirely below 0.
- **INCONCLUSIVE:** otherwise.

Reported, never criteria: the z168 form as in H-F, the 4h and 12h ICs, and the H-VR books'
replay P&L.

## Power, stated now

H-V1's CIs were about 0.17 wide on 697 cycles. On about 1,400 cycles, expect roughly 0.12.

- **H-VR:** needs its own IC above about +0.06. H-V1's own IC was +0.023, so the expected
  outcome, if nothing changed, is INCONCLUSIVE.
- **H-BS:** a repeat of H-L1's +0.17 would pass. H-F's -0.05 would not.

## What each outcome licenses (run 4 part B, one bundled restart)

- **H-VR PASS:** promote `rel_ic_dir`'s funding and L/S bias rule into the main engine. Not
  its weights, not the threshold. Anything else: the absolute bands stay.
- **H-BS BULLISH:** re-enable the basis condition in the engine's original reading.
  **BEARISH:** re-enable it with the sign flipped. **INCONCLUSIVE:** it stays disabled.
- No profit claim either way. The engine's entries are no better than random (STEP 1), and
  these two changes are component fixes, not an edge.

## Discipline

- The instrument, `scripts/run4b_eval.py`, is committed with this file. It refuses to score
  before 2027-01-16 18:44:50 UTC.
- No IC on window cycles before then. Health counts are allowed.
- A run-4 restart that changes signals before then ends the window early. It is then scored
  under the same power guard.
- Scored once. It is never re-scored on a longer window to rescue a result.

## Amendment 2026-11-19: the window restarts at the F&G-greed restart

The engine lost its −0.5 BUY penalty at F&G ≥ 70 (H-FG2 FAIL). The window was about 1.5 days
old and nothing in it was computed.

**New window:** cycles from 2026-11-19 01:13:53 UTC (`9a3d271`) whose 24h return completes
by 2027-01-18 01:13:53 UTC. Everything else is unchanged.

## Amendment 2026-11-20: the window restarts at the run-5 restart

Gold and VIX are no longer blank on weekends and at Yahoo's daily rollover (PR #66), which
changes futures signals. The owner started run 5 at the same restart. The window was about
1.6 days old and nothing in it was computed.

**New window:** cycles from 2026-11-20 15:19:44 UTC (`d954cc1`) whose 24h return completes
by 2027-01-19 15:19:44 UTC. Everything else is unchanged.
