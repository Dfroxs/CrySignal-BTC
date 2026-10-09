#!/usr/bin/env python3
"""Score the exit shadow: run-3 pre-registration addendum H-X.

Each CLOSE/HOLD opinion on an open position is valued against what actually happened:
  CLOSE  →  (P&L if closed at the opinion's price) − (the position's final P&L)
  HOLD   →  (the position's final P&L) − (P&L if closed at the opinion's price)
Positive means following the agent would have beaten the bot's own exits. Both P&Ls use
the bot's `_calc_pnl` (costs, half-close at the first target), so only the timing
differs. Only positions that have closed are scored.

Opinions on one position are hourly snapshots of the same trade, so the 90% interval
resamples POSITIONS (cluster bootstrap), not opinions.

    ./venv/bin/python scripts/exit_shadow_eval.py --db data/server.db --start <cut-off>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_POSITIONS = 8
MIN_OPINIONS = 30
N_BOOT = 5000
SEED = 2026


def decision_values(df):
    close = df["verdict"] == "CLOSE"
    return np.where(close, df["pnl_if_closed_pct"] - df["final_pnl"],
                    df["final_pnl"] - df["pnl_if_closed_pct"])


def evaluate_exits(df):
    res = {}
    rng = np.random.default_rng(SEED)
    for prov, g in df.groupby("provider"):
        g = g.assign(value=decision_values(g))
        pos = g["position_id"].unique()
        r = {"opinions": int(len(g)), "positions": int(len(pos)),
             "close_share": float((g["verdict"] == "CLOSE").mean()) if len(g) else None,
             "mean_value": float(g["value"].mean()) if len(g) else None}
        if len(pos) < MIN_POSITIONS or len(g) < MIN_OPINIONS:
            r.update(ci=None, verdict="INCONCLUSIVE")
        else:
            by_pos = {p: g.loc[g.position_id == p, "value"].to_numpy() for p in pos}
            boot = [np.concatenate([by_pos[p] for p in rng.choice(pos, len(pos))]).mean()
                    for _ in range(N_BOOT)]
            ci = [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]
            m = r["mean_value"]
            r.update(ci=ci, verdict="PASS" if m > 0 and ci[0] > 0 else
                     ("FAIL" if m <= 0 else "INCONCLUSIVE"))
        res[prov] = r
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    q = ("SELECT o.provider, o.position_id, o.verdict, o.pnl_if_closed_pct, o.error, "
         "p.pnl_pct AS final_pnl FROM shadow_exit_opinions o "
         "JOIN paper_positions p ON p.id = o.position_id WHERE p.outcome IS NOT NULL")
    df = pd.read_sql(q + (" AND o.timestamp >= ?" if args.start else ""), con,
                     params=[args.start] if args.start else [])
    errors = df["error"].notna().groupby(df["provider"]).sum().to_dict()
    df = df[df["error"].isna()].dropna(subset=["pnl_if_closed_pct", "final_pnl"])
    res = evaluate_exits(df)
    print(f"{args.db}  scored opinions={len(df)}  errors={errors}")
    for prov, r in res.items():
        print(f"{prov:10s} positions={r['positions']} opinions={r['opinions']} "
              f"CLOSE share={r['close_share']} mean value={r['mean_value']} CI={r['ci']} "
              f"→ {r['verdict']}")
    if args.out:
        args.out.write_text(json.dumps({"results": res, "errors": errors}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
