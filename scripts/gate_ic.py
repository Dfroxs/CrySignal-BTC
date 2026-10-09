#!/usr/bin/env python3
"""Do the futures entry gates earn their place under the new exit? Pre-registered in
`docs/superpowers/specs/2026-10-09-futures-gates-prereg.md`.

With the futures exit widened (trail 3.5xATR, stop/target x2), the 2025-10 → 2026-10
backtest flipped: every futures signal taken ungated came to +0.031pp per trade, the
gated ones −0.104pp, the blocked ones +0.046pp. `fakeout_first` blocked the most. That
year is where the exit was chosen, so this tests the gates on 2022-01 → 2025-10.

METHOD: one `backtest.run_backtest(..., counterfactual=True)` run, the live gate stack
and the new exit. For each gate under test, the signals it blocked ALONE (no other gate
failing) are exactly what removing that gate would admit. They are compared with the
trades the system kept. The burden is on the gate:
  FAIL          mean(only-blocked) >= mean(kept)       → remove the gate
  PASS          kept − only > 0 and its 90% CI > 0     → keep
  INCONCLUSIVE  otherwise, or fewer than MIN_ONLY      → keep
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TESTED = ("fakeout_first", "regime_counter", "trend_confluence", "psy_sl_first", "sr_first")
MIN_ONLY = 20
N_BOOT = 5000
SEED = 2026


def _resolved(t):
    from backtest import RESOLVED
    return t.get("outcome") in RESOLVED


def evaluate_gates(kept, blocked, tested=TESTED):
    rng = np.random.default_rng(SEED)
    k = np.asarray(kept, float)
    out = {}
    for g in tested:
        only = np.asarray([b["pnl_pct"] for b in blocked
                           if b.get("gates") == [g] and _resolved(b)], float)
        r = {"n_only": int(len(only)), "mean_only": float(only.mean()) if len(only) else None,
             "n_kept": int(len(k)), "mean_kept": float(k.mean()) if len(k) else None}
        if len(only) < MIN_ONLY or len(k) == 0:
            r.update(diff=None, ci=None, verdict="INCONCLUSIVE")
        else:
            diff = float(k.mean() - only.mean())
            boot = (rng.choice(k, (N_BOOT, len(k))).mean(1)
                    - rng.choice(only, (N_BOOT, len(only))).mean(1))
            ci = [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]
            v = "FAIL" if diff <= 0 else ("PASS" if ci[0] > 0 else "INCONCLUSIVE")
            r.update(diff=diff, ci=ci, verdict=v)
        out[g] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2025-10-08")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    from backtest import run_backtest
    trades, stats = run_backtest(mode="futures", timeframe="1h", start=args.start,
                                 end=args.end, counterfactual=True)
    kept = [float(t["pnl_pct"]) for t in trades if _resolved(t)]
    blocked = stats.get("blocked", [])
    res = evaluate_gates(kept, blocked)
    allb = [float(b["pnl_pct"]) for b in blocked if _resolved(b)]
    print(f"BTC futures 1h {args.start} → {args.end}   kept n={len(kept)} "
          f"mean={np.mean(kept) if kept else float('nan'):+.3f}pp   "
          f"all blocked n={len(allb)} mean={np.mean(allb) if allb else float('nan'):+.3f}pp\n")
    for g, r in res.items():
        ci = f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]" if r["ci"] else "—"
        mo = f"{r['mean_only']:+.3f}" if r["mean_only"] is not None else "—"
        df = f"{r['diff']:+.3f}" if r["diff"] is not None else "—"
        print(f"{g:17s} only-blocked n={r['n_only']:3d} mean={mo}   kept−only={df} {ci}  → {r['verdict']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"gates": res, "kept_n": len(kept),
                                        "kept_mean": float(np.mean(kept)) if kept else None,
                                        "blocked_n": len(allb),
                                        "blocked_mean": float(np.mean(allb)) if allb else None,
                                        "config": vars(args) | {"tested": TESTED, "min_only": MIN_ONLY}},
                                       indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
