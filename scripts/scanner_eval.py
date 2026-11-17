"""H-SC scorer: did Claude's scanner pick the better coins for the direction it chose?

Pre-registration: `docs/superpowers/specs/2026-11-17-claude-scanner-prereg.md`. Reads
`data/scanner.db` (pull it from the VPS), prices from Binance spot 1h klines (the local
mirror serves them).

Pick value `excess = d x (r - m)`: `r` the coin's close-to-close return from the decision
bar's close over the horizon, `m` the universe mean of the same return, `d` +1 LONG / -1
SHORT. Unit = UTC day (mean excess of that day's picks), moving-block bootstrap over days,
block 7, 5,000 draws, seed 2026. Each model is scored on its own.

    ./venv/bin/python scripts/scanner_eval.py --db data/scanner.db [--out result.json]
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

from agents.scanner import UNIVERSE  # noqa: E402

HORIZON_H = 24                         # prereg: the criterion
OTHER_H = (4, 72)                      # prereg: reported only
COST_PP = 0.20                         # prereg: futures round trip, reported only
MIN_DAYS, MIN_PICKS = 90, 300
MIN_PAIRED_DAYS = 60                   # H-SC2
MAX_ERROR_SHARE = 0.20
BLOCK, N_BOOT, SEED = 7, 5000, 2026


def load(db):
    con = sqlite3.connect(db)
    cyc = pd.read_sql("SELECT id, timestamp, bar_close, model, error, cost_usd, context "
                      "FROM scan_cycles ORDER BY bar_close", con)
    picks = pd.read_sql("SELECT cycle_id, coin, side, confidence FROM scan_picks", con)
    con.close()
    for c in ("timestamp", "bar_close"):
        cyc[c] = pd.to_datetime(cyc[c], utc=True).dt.tz_localize(None)
    return cyc, picks


def leakage_ok(cyc):
    """Every answer was stored after its bar closed, and the prompt's newest bar is it."""
    bad = []
    for r in cyc.itertuples():
        seen = pd.Timestamp(json.loads(r.context)["bar_closed_utc"])
        if not (r.timestamp > r.bar_close and seen == r.bar_close):
            bad.append(int(r.id))
    return bad


def closes_by_coin(start, end, fetch=None):
    """{coin: Series of 1h closes indexed by bar CLOSE time (UTC-naive)}."""
    if fetch is None:
        from signals.ohlcv import fetch_ohlcv_df
        fetch = fetch_ohlcv_df
    since = int((start - pd.Timedelta(hours=2)).timestamp() * 1000)
    until = int((end + pd.Timedelta(hours=max(OTHER_H) + 2)).timestamp() * 1000)
    out = {}
    for c in UNIVERSE:
        df = fetch(f"{c}/USDT", "1h", since=since, until=until)
        s = df["close"].copy()
        s.index = s.index + pd.Timedelta(hours=1)
        out[c] = s
    return out


def pick_values(cyc, picks, closes, horizon):
    """One row per LONG/SHORT pick with r, m, excess and net (both in pp)."""
    p = picks[picks.side.isin(("LONG", "SHORT"))].merge(
        cyc[["id", "bar_close", "model"]], left_on="cycle_id", right_on="id")
    rows = []
    for t, grp in p.groupby("bar_close"):
        t1 = t + pd.Timedelta(hours=horizon)
        rets = {c: (s[t1] / s[t] - 1) * 100 for c, s in closes.items()
                if t in s.index and t1 in s.index}
        if not rets:
            continue
        m = float(np.mean(list(rets.values())))
        for g in grp.itertuples():
            if g.coin not in rets:
                continue                        # prereg: excluded and counted
            d = 1 if g.side == "LONG" else -1
            r = rets[g.coin]
            rows.append({"bar_close": t, "day": t.normalize(), "model": g.model, "coin": g.coin,
                         "side": g.side, "confidence": g.confidence, "r": r, "m": m,
                         "excess": d * (r - m), "net": d * r - COST_PP})
    return pd.DataFrame(rows)


def day_values(pv, col="excess"):
    return pv.groupby("day")[col].mean().sort_index()


def block_ci(x, block=BLOCK, n_boot=N_BOOT, seed=SEED):
    """Moving-block bootstrap 90% CI of the mean of an ordered series."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return (float("nan"), float("nan"))
    b = min(block, n)
    rng = np.random.default_rng(seed)
    k = -(-n // b)
    starts = rng.integers(0, n - b + 1, size=(n_boot, k))
    idx = (starts[:, :, None] + np.arange(b)).reshape(n_boot, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return (float(np.percentile(means, 5)), float(np.percentile(means, 95)))


def verdict_of(days, n_picks, error_share):
    """(verdict, mean, ci) per the prereg."""
    mean = float(np.mean(days)) if len(days) else float("nan")
    ci = block_ci(days)
    if error_share > MAX_ERROR_SHARE or len(days) < MIN_DAYS or n_picks < MIN_PICKS:
        return "INCONCLUSIVE", mean, ci
    if mean <= 0:
        return "FAIL", mean, ci
    return ("PASS" if ci[0] > 0 else "INCONCLUSIVE"), mean, ci


def paired_verdict(days_pure, days_hybrid, err_pure, err_hybrid):
    """H-SC2: diff = hybrid - pure on days both arms picked. (verdict, mean, ci, n_days)."""
    both = days_pure.index.intersection(days_hybrid.index)
    diff = (days_hybrid[both] - days_pure[both]).sort_index().values
    mean = float(np.mean(diff)) if len(diff) else float("nan")
    ci = block_ci(diff)
    if max(err_pure, err_hybrid) > MAX_ERROR_SHARE or len(diff) < MIN_PAIRED_DAYS:
        return "INCONCLUSIVE", mean, ci, len(diff)
    if mean <= 0:
        return "FAIL", mean, ci, len(diff)
    return ("PASS" if ci[0] > 0 else "INCONCLUSIVE"), mean, ci, len(diff)


def arm_days(cyc, picks, closes, start=None):
    ok = cyc[cyc.error.isna()]
    if start is not None:
        ok = ok[ok.bar_close >= start]
    pv = pick_values(ok, picks[picks.cycle_id.isin(ok.id)], closes, HORIZON_H)
    days = day_values(pv) if len(pv) else pd.Series(dtype=float)
    span = cyc if start is None else cyc[cyc.bar_close >= start]
    return days, float(span.error.notna().mean()) if len(span) else 0.0


def score(cyc, picks, closes):
    out = {}
    for model, cm in cyc.groupby(cyc.model.fillna("(error)"), sort=False):
        if model == "(error)":
            continue
        span = cyc[(cyc.bar_close >= cm.bar_close.min()) & (cyc.bar_close <= cm.bar_close.max())]
        err = float(span.error.notna().mean())
        ok = cm[cm.error.isna()]
        pk = picks[picks.cycle_id.isin(ok.id)]
        pv = pick_values(ok, pk, closes, HORIZON_H)
        days = day_values(pv) if len(pv) else pd.Series(dtype=float)
        v, mean, ci = verdict_of(days.values, len(pv), err)
        rep = {"verdict": v, "mean_day_excess_pp": mean, "ci90": ci, "days": int(len(days)),
               "picks": int(len(pv)), "cycles": int(len(cm)), "error_share": err,
               "none_share": float((pk.side == "NONE").mean()) if len(pk) else None,
               "cost_usd": float(cm.cost_usd.fillna(0).sum())}
        if len(pv):
            rep["net_per_pick_pp"] = float(pv.net.mean())
            rep["net_per_day_pp"] = float(day_values(pv, "net").mean())
            rep["by_side"] = {s: {"n": int(len(g)), "excess": float(g.excess.mean())}
                              for s, g in pv.groupby("side")}
            terc = pd.qcut(pv.confidence.rank(method="first"), 3, labels=["low", "mid", "high"]) \
                if len(pv) >= 3 else None
            if terc is not None:
                rep["by_confidence"] = {str(k): float(g.excess.mean())
                                        for k, g in pv.groupby(terc, observed=True)}
            rep["btc_vs_rest"] = {"BTC": float(pv[pv.coin == "BTC"].excess.mean())
                                  if (pv.coin == "BTC").any() else None,
                                  "rest": float(pv[pv.coin != "BTC"].excess.mean())
                                  if (pv.coin != "BTC").any() else None}
        for h in OTHER_H:
            ph = pick_values(ok, pk, closes, h)
            rep[f"excess_{h}h"] = float(day_values(ph).mean()) if len(ph) else None
        out[model] = rep
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/scanner.db")
    ap.add_argument("--out")
    ap.add_argument("--hybrid-db", help="H-SC2: also score the hybrid arm against the pure one")
    args = ap.parse_args(argv)
    cyc, picks = load(args.db)
    if cyc.empty:
        print("no scan cycles")
        return 1
    bad = leakage_ok(cyc)
    if bad:
        print(f"LEAKAGE CHECK FAILED for cycles {bad[:10]} ({len(bad)}): run discarded")
        return 2
    closes = closes_by_coin(cyc.bar_close.min(), cyc.bar_close.max())
    res = score(cyc, picks, closes)
    print(f"source {args.db}: {len(cyc)} cycles, {cyc.bar_close.min()} .. {cyc.bar_close.max()}")
    for model, r in res.items():
        print(f"\n{model}: {r['verdict']}  mean day excess {r['mean_day_excess_pp']:+.3f}pp "
              f"CI90 [{r['ci90'][0]:+.3f}, {r['ci90'][1]:+.3f}]  days {r['days']}/{MIN_DAYS} "
              f"picks {r['picks']}/{MIN_PICKS}  errors {r['error_share']:.1%}")
        for k in ("net_per_pick_pp", "net_per_day_pp", "none_share", "by_side", "by_confidence",
                  "btc_vs_rest", "excess_4h", "excess_72h", "cost_usd"):
            if k in r:
                print(f"  {k}: {r[k]}")
    if args.hybrid_db and Path(args.hybrid_db).exists():
        hc, hp = load(args.hybrid_db)
        bad = leakage_ok(hc)
        if bad:
            print(f"H-SC2 LEAKAGE CHECK FAILED for hybrid cycles {bad[:10]}: discarded")
        elif not hc.empty:
            start = hc.bar_close.min()          # pairs only from the hybrid arm's first cycle
            closes = closes_by_coin(min(cyc.bar_close.min(), start), max(cyc.bar_close.max(), hc.bar_close.max()))
            dp, ep = arm_days(cyc, picks, closes, start)
            dh, eh = arm_days(hc, hp, closes)
            v, mean, ci, n = paired_verdict(dp, dh, ep, eh)
            res["H-SC2"] = {"verdict": v, "mean_diff_pp": mean, "ci90": ci, "paired_days": n,
                            "errors": {"pure": ep, "hybrid": eh}}
            print(f"\nH-SC2 hybrid - pure: {v}  mean {mean:+.3f}pp CI90 [{ci[0]:+.3f}, {ci[1]:+.3f}]  "
                  f"paired days {n}/{MIN_PAIRED_DAYS}")
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
