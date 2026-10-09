#!/usr/bin/env python3
"""Is the futures exit too tight? Pre-registered in
`docs/superpowers/specs/2026-10-09-futures-exit-prereg.md`.

On 2025-10 → 2026-10, BTC futures shorts were not late: they entered into bounces (+1.13%
over the prior 24h, upper part of the range), and their 72h direction slightly beat
random. They lost because the 1.5×ATR trail from entry, about 1.1% on 1h bars, sits
inside BTC's normal adverse move (~2.4%). Widening the INITIAL stop alone changed
nothing, because the trail is what closes the trade. Exits were tested in 09-21, but only
AFTER TP1, never this trail.

ARMS, on the identical signal set (every signal backtest.run_backtest simulates, taken
and gate-blocked alike, recorded at the call):
  base       the signal exactly as the engine built it, shipped exit params
  candidate  stop and target distances × STOP_MULT, trailing_atr_factor = TRAIL

STATISTIC: the paired difference candidate − base per signal, among signals resolved in
both arms. Pairing removes the entry from the comparison; only the exit differs.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest  # noqa: E402
from backtest import MAX_HOLD_CANDLES, RESOLVED  # noqa: E402

STOP_MULT = 2.0
TRAIL = 3.5
MIN_N = 100
MIN_DIR_N = 20
N_BOOT = 5000
SEED = 2026


def widen(signal, mult):
    """Copy of `signal` with stop and target distances from entry scaled by `mult`."""
    s = copy.deepcopy(signal)
    e = s["entry_price"]
    for k in ("stop_loss", "take_profit", "tp2"):
        if s.get(k):
            s[k] = e + (s[k] - e) * mult
    return s


def record_signals(mode, timeframe, start, end):
    """(df, [(entry_idx, signal)]) for every signal run_backtest simulates.

    Recorded by wrapping backtest._simulate_forward for the duration of one run, so the
    set is exactly what the backtest saw (taken and counterfactual), not a re-derivation.
    """
    calls, frame = [], {}
    original = backtest._simulate_forward

    def spy(df, entry_idx, signal, *a, **kw):
        frame["df"] = df
        calls.append((entry_idx, copy.deepcopy(signal)))
        return original(df, entry_idx, signal, *a, **kw)

    backtest._simulate_forward = spy
    try:
        backtest.run_backtest(mode=mode, timeframe=timeframe, start=start, end=end,
                              counterfactual=True)
    finally:
        backtest._simulate_forward = original
    return frame.get("df"), calls


def paired(base, cand):
    """[(k, base_pnl, cand_pnl)] for positions k resolved in both arms."""
    out = []
    for k, (b, c) in enumerate(zip(base, cand)):
        if b and c and b["outcome"] in RESOLVED and c["outcome"] in RESOLVED:
            out.append((k, float(b["pnl_pct"]), float(c["pnl_pct"])))
    return out


def verdict(n, mean, ci, windows, by_dir):
    if n < MIN_N:
        return "INCONCLUSIVE"
    if mean <= 0:
        return "FAIL"
    judged = [m for (dn, m) in by_dir.values() if dn >= MIN_DIR_N]
    if ci[0] > 0 and sum(w > 0 for w in windows) >= 3 and all(m > 0 for m in judged):
        return "PASS"
    return "INCONCLUSIVE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-10-08")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    tf, mode = "1h", "futures"
    df, calls = record_signals(mode, tf, args.start, args.end)
    if df is None or not calls:
        print("no signals recorded")
        return 1
    hold = MAX_HOLD_CANDLES[tf]
    base = [backtest._simulate_forward(df, i, s, hold, tf, mode) for i, s in calls]
    cand = [backtest._simulate_forward(df, i, widen(s, STOP_MULT), hold, tf, mode,
                                       exit_params={"trailing_atr_factor": TRAIL})
            for i, s in calls]
    pairs = paired(base, cand)
    d = np.array([c - b for _, b, c in pairs])
    n = len(d)
    rng = np.random.default_rng(SEED)
    boot = rng.choice(d, size=(N_BOOT, n)).mean(axis=1) if n else np.array([0.0])
    ci = (float(np.percentile(boot, 5)), float(np.percentile(boot, 95)))
    windows = [float(w.mean()) for w in np.array_split(d, 4)] if n >= 4 else []
    by_dir = {}
    for dirn in ("SELL", "BUY"):
        sel = np.array([c - b for k, b, c in pairs if calls[k][1]["type"] == dirn])
        by_dir[dirn] = (len(sel), float(sel.mean()) if len(sel) else 0.0)
    v = verdict(n, float(d.mean()) if n else 0.0, ci, windows, by_dir)

    bp = np.array([b for _, b, _ in pairs]); cp = np.array([c for _, _, c in pairs])
    print(f"BTC futures 1h  {args.start} → {args.end}  signals recorded={len(calls)}  "
          f"paired={n}  (stop ×{STOP_MULT}, trail {TRAIL}×ATR)\n")
    print(f"base       mean {bp.mean():+.3f}pp  win {np.mean(bp > 0):.1%}")
    print(f"candidate  mean {cp.mean():+.3f}pp  win {np.mean(cp > 0):.1%}")
    print(f"\npaired diff  {d.mean():+.3f}pp  90% CI [{ci[0]:+.3f}, {ci[1]:+.3f}]")
    print("windows      " + "  ".join(f"{w:+.3f}" for w in windows))
    for k, (dn, m) in by_dir.items():
        print(f"{k:5s}        n={dn}  diff {m:+.3f}pp")
    print(f"\nVERDICT: {v}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "verdict": v, "n": n, "mean_diff": float(d.mean()) if n else None, "ci90": ci,
            "windows": windows, "by_dir": by_dir, "signals_recorded": len(calls),
            "base_mean": float(bp.mean()) if n else None,
            "candidate_mean": float(cp.mean()) if n else None,
            "config": {"start": args.start, "end": args.end, "stop_mult": STOP_MULT,
                       "trail": TRAIL, "min_n": MIN_N, "seed": SEED},
        }, indent=1))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
