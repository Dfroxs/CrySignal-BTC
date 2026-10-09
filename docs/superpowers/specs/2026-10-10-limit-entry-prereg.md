# Pre-registration — do limit-order entries beat market entries on futures? (H-LO)

**Written 2026-10-10, before the instrument exists and before any figure below has been
computed.** The only counts known in advance are H-CT's: the configured engine fired
338 (primary) and 117 (confirmation) de-duplicated, resolved futures entries on this data
(`2026-10-09-counter-trend-run/result.json`, the `kept` arms). No outcome of this test
was known.

## Why

Costs are ~40% of the losses. Futures with the new exit stands at about −0.05pp per
trade. A futures market entry pays taker fee 0.04% plus slippage 0.05% (`EXECUTION_CONFIG`).
A post-only limit at the signal price pays maker fee 0.02% and no slippage: **+0.07pp
per filled trade**, or +0.08pp if the real taker fee is 0.05%. But a limit order is not a
free saving. It fills when price comes back to it, and misses when price runs away at once
in the trade's direction. If the misses are the winners, adverse selection eats the saving.
That trade-off is what this test measures.

## Data (fixed now)

- `docs/superpowers/specs/2026-10-09-hist-futures-ic-run/perp_1h.csv` (BTCUSDT perp 1h,
  sha256 in that folder). HTF 4h + 1D resampled from it, exactly as `scripts/veto_ic.py`
  loads it.
- **Primary:** 2021-01-01 → 2025-01-01. **Confirmation:** 2025-01-01 → 2026-08-30. These
  are the H-CT windows.

## Entries

Every candle is scored by `backtest._score_candle` with the engine as deployed (futures,
`SIGNAL_THRESHOLD`, counter-trend veto on, every confidence tier since futures opens from
WEAK). Phase 3 gates are not applied in the primary set. The subset that passes
`_failing_gates` (re-entry not simulated) is reported descriptively. Each fire goes through
`_exit_signal` → `_simulate_forward` (live futures exit and costs), de-duplicated per
direction as in `veto_ic.simulate`. OPEN rows are excluded. This is the **market arm**,
with P&L `m_i`.

## Limit arm (paired, same entries)

For each market-arm entry at bar i, with L = close[i] (the signal's entry price), a
post-only limit is resting for bar i+1 only:

- **Fill:** BUY if low[i+1] ≤ L × (1 − 0.0001); SELL if high[i+1] ≥ L × (1 + 0.0001).
  The 1bp through-trade stands in for queue position, since a touch is not a fill.
- **Conservative ambiguity rule:** if bar i+1 also reaches the trade's TP1 (BUY:
  high ≥ take_profit; SELL: low ≤ take_profit, after `_exit_signal`), the order of events
  is unknown. It is counted as **unfilled**.
- **Filled:** the path is the market trade's path from the same price, so
  `l_i = m_i + s`, with s = (taker + slippage) − maker = 0.04 + 0.05 − 0.02 = **0.07pp**.
  Exits stay taker.
- **Unfilled:** no trade, so `l_i = 0`. A replacement entry the market arm's sequence did
  not take is not credited, which biases against the limit arm.

## Hypothesis

**H-LO1:** d_i = l_i − m_i, per window. Mean d̄ with a 90% iid bootstrap interval (5,000
resamples, seed 2026).
- **PASS** if d̄ > 0 **and** the interval lies above 0.
- **FAIL** if d̄ ≤ 0.
- **INCONCLUSIVE** otherwise.

**Decision across windows:** limit entries **PASS** (licensed for run 4) only if the
primary PASSes **and** the confirmation d̄ > 0. They **FAIL** if the primary FAILs.
Anything else is INCONCLUSIVE, and market entries stay.

**Power guard:** n < 100 (primary) or < 30 (confirmation) makes that window INCONCLUSIVE.

## Reported regardless, never criteria

Fill rate; mean m_i of filled and of unfilled entries (the adverse-selection gap); the
ambiguity-rule count; d̄ by direction; d̄ at taker 0.05% (s = 0.08); d̄ on the
Phase-3-passing subset; d̄ per year.

## Discard

- Any entry whose fill test reads a bar other than i+1.
- If any filled l_i − m_i ≠ s (to 1e-9), the instrument diverged from this design.
