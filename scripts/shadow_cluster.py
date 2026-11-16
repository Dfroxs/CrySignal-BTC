#!/usr/bin/env python3
"""H-S, descriptive follow-up: the same primary statistic with opinions grouped into episodes.

NOT a criterion and it does not change the H-S verdict (`scripts/shadow_eval.py` decides).
`shadow_eval` bootstraps opinions as if independent, but the bot asks the agent every hour
about the same standing signal, so consecutive opinions share one price path. Here the
per-opinion trade P&L is the one `shadow_eval` computes (same replay, same exits), and
the resampling unit is the episode: a maximal run of opinions with the same mode and
signal direction whose consecutive gap is below GAP hours. Three groupings, fixed before
the numbers were looked at: GAP 12h, GAP 24h, and calendar day (UTC).

    ./venv/bin/python scripts/shadow_cluster.py --db data/snap_futures.db --start "2026-10-17 14:39:46" --out <dir>
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
from scripts.shadow_eval import BAR, TF, N_BOOT, SEED, _hourly_prices, signed_forward, trade_outcomes  # noqa: E402

PROVIDER = "anthropic"


def episodes(ops, gap_h):
    """Episode id per row: runs of the same (mode, signal_type) with gaps < gap_h hours."""
    ids = np.zeros(len(ops), dtype=int)
    cur, last_key, last_ts = -1, None, None
    for k, (mode, typ, ts) in enumerate(zip(ops["mode"], ops["signal_type"], ops["ts"])):
        key = (mode, typ)
        if key != last_key or last_ts is None or (ts - last_ts) >= pd.Timedelta(hours=gap_h):
            cur += 1
        ids[k], last_key, last_ts = cur, key, ts
    return ids


def diff_of(sub):
    a = sub.loc[sub.verdict == "AGREE", "pnl"]
    d = sub.loc[sub.verdict == "DISAGREE", "pnl"]
    return (a.mean() - d.mean()) if len(a) and len(d) else np.nan


def cluster_boot(df, cid_col, rng):
    groups = [g for _, g in df.groupby(cid_col)]
    out = []
    for _ in range(N_BOOT):
        pick = rng.integers(0, len(groups), len(groups))
        d = diff_of(pd.concat([groups[i] for i in pick]))
        if d == d:
            out.append(d)
    out = np.array(out)
    return [float(np.percentile(out, 5)), float(np.percentile(out, 95))], len(out)


def summarise(df, cid_col, label, rng):
    ep = df.groupby(cid_col).agg(n=("pnl", "size"), agree=("verdict", lambda v: int((v == "AGREE").sum())),
                                 mean=("pnl", "mean"), start=("ts", "min"), mode=("mode", "first"),
                                 typ=("signal_type", "first"))
    ci, n_ok = cluster_boot(df, cid_col, rng)
    # leave-one-episode-out: the diff when each episode is dropped in turn
    loo = {int(i): diff_of(df[df[cid_col] != i]) for i in ep.index}
    ag_eps = ep[ep.agree > 0]
    return {"grouping": label, "episodes": int(len(ep)), "episodes_with_agree": int(len(ag_eps)),
            "diff": float(diff_of(df)), "ci90_cluster": ci, "boot_draws_used": n_ok,
            "ci_excludes_zero": bool(ci[0] > 0),
            "leave_one_out_min": float(np.nanmin(list(loo.values()))),
            "leave_one_out_max": float(np.nanmax(list(loo.values()))),
            "leave_one_out_worst_episode_start": str(ep.loc[min(loo, key=lambda k: loo[k] if loo[k] == loo[k] else 9e9), "start"]),
            "agree_episodes": [{"start": str(r["start"]), "mode": r["mode"], "type": r["typ"], "n": int(r["n"]),
                                "agree": int(r["agree"]), "mean_pnl": float(r["mean"])}
                               for _, r in ag_eps.iterrows()]}


def strat_diff(sub):
    """AGREE-weighted mean of within-(mode, direction) AGREE-DISAGREE differences."""
    num = den = 0.0
    for _, g in sub.groupby(["mode", "signal_type"]):
        a, d = g.loc[g.verdict == "AGREE", "pnl"], g.loc[g.verdict == "DISAGREE", "pnl"]
        if len(a) and len(d):
            num += len(a) * (a.mean() - d.mean())
            den += len(a)
    return num / den if den else np.nan


def within_diff(sub, col):
    """Difference after removing each episode's mean P&L, episodes with both verdicts only."""
    has = sub.groupby(col)["verdict"].transform(lambda v: v.nunique() == 2)
    g = sub[has].copy()
    if g.empty:
        return np.nan
    g["dm"] = g["pnl"] - g.groupby(col)["pnl"].transform("mean")
    a, d = g.loc[g.verdict == "AGREE", "dm"], g.loc[g.verdict == "DISAGREE", "dm"]
    return a.mean() - d.mean() if len(a) and len(d) else np.nan


POST_N = 1000   # post-hoc section only; the pandas resampling is slow


def boot_stat(df, col, fn, rng):
    groups = [g for _, g in df.groupby(col)]
    out = []
    for _ in range(POST_N):
        pick = rng.integers(0, len(groups), len(groups))
        x = pd.concat([g.assign(_b=k) for k, g in enumerate(groups[i] for i in pick)])
        v = fn(x, "_b") if fn is within_diff else fn(x)
        if v == v:
            out.append(v)
    return [float(np.percentile(out, 5)), float(np.percentile(out, 95))]


def perm_within(df, col, rng):
    obs = within_diff(df, col)
    cnt = 0
    for _ in range(POST_N):
        x = df.copy()
        x["verdict"] = x.groupby(col)["verdict"].transform(lambda v: rng.permutation(v.to_numpy()))
        cnt += within_diff(x, col) >= obs
    return float((cnt + 1) / (POST_N + 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    q = ("SELECT timestamp, provider, verdict, mode, signal_type, entry_price, stop_loss, take_profit, atr, error "
         f"FROM shadow_opinions WHERE {ts_sql('timestamp')} >= ? AND provider = ? ORDER BY timestamp")
    ops = pd.read_sql(q, con, params=[norm_ts(args.start), PROVIDER])
    ops = ops[ops["error"].isna()].copy()
    ops["ts"] = pd.to_datetime(ops["timestamp"].str.replace("T", " ").str[:19])
    ops["fwd"] = signed_forward(ops, _hourly_prices(con))
    from backtest import _simulate_forward
    from signals.ohlcv import fetch_ohlcv_df
    since = int((ops["ts"].min() - pd.Timedelta(days=12)).timestamp() * 1000)
    frames = {m: fetch_ohlcv_df("BTC/USDT", tf, since=since, vwap_period=24 if tf == "1h" else 6)
              for m, tf in TF.items() if (ops["mode"] == m).any()}
    ops["pnl"] = trade_outcomes(ops, frames, _simulate_forward)
    args.out.mkdir(parents=True, exist_ok=True)
    ops.to_csv(args.out / "opinions.csv", index=False)
    df = ops.dropna(subset=["pnl"]).reset_index(drop=True)
    print(f"opinions scored {len(df)} of {len(ops)}  AGREE {int((df.verdict=='AGREE').sum())}  "
          f"DISAGREE {int((df.verdict=='DISAGREE').sum())}")
    df["ep12"] = episodes(df, 12)
    df["ep24"] = episodes(df, 24)
    df["day"] = pd.factorize(df["ts"].dt.strftime("%Y-%m-%d") + df["mode"])[0]   # calendar day, split by mode
    res = {"as_registered_iid_diff": float(diff_of(df)), "groupings": []}
    rng = np.random.default_rng(SEED)
    for col, label in (("ep12", "mode+direction runs, gap<12h"), ("ep24", "mode+direction runs, gap<24h"),
                       ("day", "calendar day (UTC) x mode")):
        s = summarise(df, col, label, rng)
        res["groupings"].append(s)
        print(f"\n{label}: episodes={s['episodes']} (with AGREE {s['episodes_with_agree']})  "
              f"diff={s['diff']:+.3f}  cluster CI90 [{s['ci90_cluster'][0]:+.3f}, {s['ci90_cluster'][1]:+.3f}]  "
              f"leave-one-out range [{s['leave_one_out_min']:+.3f}, {s['leave_one_out_max']:+.3f}]")
        for e in s["agree_episodes"]:
            print(f"   AGREE episode {e['start']} {e['mode']:7s}{e['type']:5s} n={e['n']:3d} agree={e['agree']:2d} mean={e['mean_pnl']:+.2f}")
    # futures only (the registered statistic mixes modes)
    fu = df[df["mode"] == "futures"].copy()
    fu["ep12"] = episodes(fu, 12)
    s = summarise(fu, "ep12", "futures only, runs gap<12h", rng)
    res["futures_only"] = s
    print(f"\nfutures only: opinions={len(fu)} AGREE={int((fu.verdict=='AGREE').sum())} episodes={s['episodes']} "
          f"diff={s['diff']:+.3f}  cluster CI90 [{s['ci90_cluster'][0]:+.3f}, {s['ci90_cluster'][1]:+.3f}]")
    # by mode and direction, descriptive means
    tab = df.groupby(["mode", "signal_type", "verdict"])["pnl"].agg(["size", "mean"]).round(3)
    print("\nby mode / direction / verdict:\n" + tab.to_string())
    res["by_group"] = json.loads(tab.reset_index().to_json(orient="records"))
    # POST-HOC (added after the pooled result showed direction composition): see the doc
    post = {}
    for col, label in (("ep12", "gap<12h"), ("day", "calendar day")):
        sd, ci_s = strat_diff(df), boot_stat(df, col, strat_diff, rng)
        wd, ci_w = within_diff(df, col), boot_stat(df, col, within_diff, rng)
        p = perm_within(df, col, rng)
        post[label] = {"stratified_diff": float(sd), "stratified_ci90": ci_s,
                       "within_episode_diff": float(wd), "within_episode_ci90": ci_w,
                       "within_episode_perm_p_one_sided": p}
        print(f"\n[{label}] stratified by mode+direction: {sd:+.3f} CI90 [{ci_s[0]:+.3f}, {ci_s[1]:+.3f}]"
              f" | within-episode: {wd:+.3f} CI90 [{ci_w[0]:+.3f}, {ci_w[1]:+.3f}]  perm p(one-sided)={p:.4f}")
    res["post_hoc"] = post
    (args.out / "hs_cluster.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
