# Results — is the futures exit too tight?

Scored against `2026-10-09-futures-exit-prereg.md`, committed at `279c6b5` before any
test-window figure existed. The run started at that commit. Raw output: `2026-10-09-futures-exit-run/run.txt`
and `result.json`.

## Verdict: PASS

| criterion | required | measured | |
|---|---|---|---|
| paired signals | ≥ 100 | **417** | ✓ |
| mean d, 90% CI | > 0, CI above 0 | **+0.243pp** [+0.054, +0.436] | ✓ |
| sequential windows positive | ≥ 3 of 4 | **4 of 4** (+0.714, +0.038, +0.094, +0.122) | ✓ |
| no direction reversal | SELL and BUY > 0 | SELL **+0.311** (n=170), BUY **+0.197** (n=247) | ✓ |

**Discard check:** the base replay matched `run_backtest`'s own P&L on all 417
signals. See the fidelity line in `run.txt`.

## Reported as promised: absolute P&L

| arm | mean per trade | win rate |
|---|---:|---:|
| shipped (trail 1.5×ATR) | −0.296pp | 30.0% |
| candidate (stop ×2, trail 3.5×ATR) | **−0.053pp** | 35.3% |

**The wider exit is better than the shipped one. It does not make futures profitable.**
After costs, the candidate still loses 0.05pp per trade on this window. The exit was
destroying about 0.24pp of what the entries carried. Removing that brings futures to
roughly breakeven, not to an edge.

## What was licensed and shipped (run 3, on `develop`)

- `FUTURES_CONFIG["trailing_atr_factor"]`: 1.5 → **3.5**.
- `FUTURES_CONFIG["stop_distance_mult"]` = **2.0**, applied by
  `trading/paper.apply_futures_exit_geometry` at both futures open sites in
  `run_bot.py`, **after** every Phase 3 gate, exactly as tested. On a fresh open it is
  applied before the aggregate-risk cap, so the cap sees the stop actually used.
- `backtest.py` and `scripts/variant_books.py` simulate futures through the same
  helper, so replays keep mirroring live. Spot is untouched.

## Note for anyone re-running `scripts/trail_ic.py`

It records signals by wrapping `_simulate_forward`, and after this change `backtest.py`
already widens futures signals before that call. A re-run on current code would
compare ×2 against ×4, not the shipped exit against the candidate. The scored result
belongs to commit `279c6b5`. To reproduce it, check out that commit.
