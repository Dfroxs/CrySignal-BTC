#!/usr/bin/env python3
"""Score the shadow agents: run-3 pre-registration § H-S.

For each stored opinion without an error: the forward 24h return in the SIGNAL's
direction, from its entry price to the futures cycle price 24h later (from cycle_log,
so no network is needed). Per provider: mean(AGREE) − mean(DISAGREE), with a 90%
bootstrap interval. An agent earns attention only if the signals it agrees with do
better than the ones it rejects.

    ./venv/bin/python scripts/shadow_eval.py --db data/server.db --start <run-3 started_at>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_PER_GROUP = 20
N_BOOT = 5000
SEED = 2026


def _hourly_prices(con):
    p = pd.read_sql("SELECT timestamp, price FROM cycle_log WHERE mode='futures' ORDER BY timestamp", con)
    p.index = pd.to_datetime(p["timestamp"].str.replace("T", " ").str[:19]).dt.floor("h")
    return p["price"][~p.index.duplicated(keep="first")]


def signed_forward(ops, prices, horizon_h=24):
    """Percent move from entry to the price `horizon_h` later, positive when the
    signal's direction was right. NaN when that hour has no cycle."""
    out = []
    for ts, typ, entry in zip(ops["ts"], ops["signal_type"], ops["entry_price"]):
        later = prices.get(pd.Timestamp(ts).floor("h") + pd.Timedelta(hours=horizon_h))
        if later is None or not entry or later != later:
            out.append(float("nan"))
            continue
        move = (later / entry - 1) * 100
        out.append(-move if typ == "SELL" else move)
    return out


def evaluate(df):
    """{provider: stats} from a frame with provider, verdict, fwd."""
    res = {}
    rng = np.random.default_rng(SEED)
    for prov, g in df.dropna(subset=["fwd"]).groupby("provider"):
        a = g.loc[g.verdict == "AGREE", "fwd"].to_numpy()
        d = g.loc[g.verdict == "DISAGREE", "fwd"].to_numpy()
        r = {"n_agree": len(a), "n_disagree": len(d),
             "mean_agree": float(a.mean()) if len(a) else None,
             "mean_disagree": float(d.mean()) if len(d) else None}
        if len(a) < MIN_PER_GROUP or len(d) < MIN_PER_GROUP:
            r.update(diff=None, ci=None, verdict="INCONCLUSIVE")
        else:
            diff = float(a.mean() - d.mean())
            boot = (rng.choice(a, (N_BOOT, len(a))).mean(1) - rng.choice(d, (N_BOOT, len(d))).mean(1))
            ci = [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]
            v = "PASS" if diff > 0 and ci[0] > 0 else ("FAIL" if diff <= 0 else "INCONCLUSIVE")
            r.update(diff=diff, ci=ci, verdict=v)
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
    q = "SELECT timestamp, provider, model, verdict, signal_type, entry_price, error FROM shadow_opinions"
    ops = pd.read_sql(q + (" WHERE timestamp >= ?" if args.start else ""), con,
                      params=[args.start] if args.start else [])
    errors = ops["error"].notna().groupby(ops["provider"]).sum().to_dict()
    ops = ops[ops["error"].isna()].copy()
    ops["ts"] = pd.to_datetime(ops["timestamp"].str.replace("T", " ").str[:19])
    ops["fwd"] = signed_forward(ops, _hourly_prices(con))
    res = evaluate(ops)
    print(f"{args.db}  opinions={len(ops)}  errors by provider={errors}\n")
    for prov, r in res.items():
        ci = f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]" if r["ci"] else "—"
        diff = f"{r['diff']:+.3f}" if r["diff"] is not None else "—"
        print(f"{prov:10s} AGREE n={r['n_agree']} mean={r['mean_agree']}  "
              f"DISAGREE n={r['n_disagree']} mean={r['mean_disagree']}  "
              f"diff={diff} {ci}  → {r['verdict']}")
    if args.out:
        args.out.write_text(json.dumps({"results": res, "errors": errors}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
