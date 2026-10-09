#!/usr/bin/env python3
"""Score the shadow agents: run-3 pre-registration § H-S.

PRIMARY (amended 2026-10-09, before any shadow opinion existed): the trade the bot
would have run. Each opinion's signal is replayed through `backtest._simulate_forward`,
the bot's own exits per mode (spot 4h, futures 1h, the stored stop/target levels it was
judged on, trailing, hold cap, costs), entered on the bar the bot scored. Per provider:
mean(AGREE) − mean(DISAGREE) of that net P&L, with a 90% bootstrap interval.

DESCRIPTIVE: the signed 24h return from cycle_log, as originally registered. It no
longer decides anything: neither mode closes trades on a 24h clock.

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.live_ic import norm_ts, ts_sql  # noqa: E402

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


TF = {"futures": "1h", "spot": "4h"}
BAR = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}


def trade_outcomes(ops, frames, sim_fn):
    """Net P&L of each opinion's trade under the bot's exits; NaN when it cannot be
    replayed (no frame for the mode, bar missing, or still open at the data's end)."""
    from backtest import MAX_HOLD_CANDLES, RESOLVED
    out = []
    for row in ops.itertuples(index=False):
        tf = TF.get(row.mode)
        df = frames.get(row.mode)
        bar = pd.Timestamp(row.ts).floor(tf) - BAR[tf] if tf else None
        if df is None or bar not in df.index:
            out.append(float("nan"))
            continue
        sig = {"type": row.signal_type, "entry_price": row.entry_price,
               "stop_loss": row.stop_loss, "take_profit": row.take_profit, "atr": row.atr}
        t = sim_fn(df, int(df.index.get_loc(bar)), sig, MAX_HOLD_CANDLES[tf], tf, row.mode)
        out.append(float(t["pnl_pct"]) if t and t["outcome"] in RESOLVED else float("nan"))
    return out


def evaluate(df, col="fwd"):
    """{provider: stats} from a frame with provider, verdict, fwd."""
    res = {}
    rng = np.random.default_rng(SEED)
    for prov, g in df.dropna(subset=[col]).groupby("provider"):
        a = g.loc[g.verdict == "AGREE", col].to_numpy()
        d = g.loc[g.verdict == "DISAGREE", col].to_numpy()
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
    q = ("SELECT timestamp, provider, model, verdict, mode, signal_type, entry_price, "
         "stop_loss, take_profit, atr, error FROM shadow_opinions")
    ops = pd.read_sql(q + (f" WHERE {ts_sql('timestamp')} >= ?" if args.start else ""), con,
                      params=[norm_ts(args.start)] if args.start else [])
    errors = ops["error"].notna().groupby(ops["provider"]).sum().to_dict()
    ops = ops[ops["error"].isna()].copy()
    ops["ts"] = pd.to_datetime(ops["timestamp"].str.replace("T", " ").str[:19])
    ops["fwd"] = signed_forward(ops, _hourly_prices(con))
    from backtest import _simulate_forward
    from signals.ohlcv import fetch_ohlcv_df
    frames = {}
    if len(ops):
        since = int((ops["ts"].min() - pd.Timedelta(days=12)).timestamp() * 1000)
        for mode, tf in TF.items():
            if (ops["mode"] == mode).any():
                frames[mode] = fetch_ohlcv_df("BTC/USDT", tf, since=since,
                                              vwap_period=24 if tf == "1h" else 6)
    ops["pnl"] = trade_outcomes(ops, frames, _simulate_forward)
    res = evaluate(ops, "pnl")
    desc = evaluate(ops, "fwd")
    print(f"{args.db}  opinions={len(ops)}  errors by provider={errors}\n")
    print("PRIMARY — simulated trade under the bot's exits")
    for prov, r in res.items():
        ci = f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]" if r["ci"] else "—"
        diff = f"{r['diff']:+.3f}" if r["diff"] is not None else "—"
        print(f"{prov:10s} AGREE n={r['n_agree']} mean={r['mean_agree']}  "
              f"DISAGREE n={r['n_disagree']} mean={r['mean_disagree']}  "
              f"diff={diff} {ci}  → {r['verdict']}")
    print("\ndescriptive — signed 24h return (never a criterion)")
    for prov, r in desc.items():
        print(f"{prov:10s} AGREE n={r['n_agree']} mean={r['mean_agree']}  "
              f"DISAGREE n={r['n_disagree']} mean={r['mean_disagree']}")
    if args.out:
        args.out.write_text(json.dumps({"results": res, "descriptive_24h": desc,
                                        "errors": errors}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
