"""H-SCD: why do the scanner's picks win or lose? Diagnosis of H-SC's own window.

Pre-registration: `docs/superpowers/specs/2026-11-19-scanner-diagnosis-prereg.md`. Runs on
H-SC's scoring date, after H-SC is scored, from `agents.scoring_day`; never earlier. Built
and tested on synthetic data only.

Two phases, each run once:
  --phase discovery   Parts A, C, D, E, F, and Part B's candidates on the first half of
                      scored days. Writes the discovery file and prints its SHA-256.
  --phase confirm     Part B's candidates on the second half, with the discovery edges.
                      Refuses unless the discovery file's SHA-256 equals --sha.

    ./venv/bin/python scripts/scanner_diag.py --phase discovery --out data/scoring/H-SCD-1.json \
        --discovery data/scoring/H-SCD-discovery.json
    ./venv/bin/python scripts/scanner_diag.py --phase confirm --out data/scoring/H-SCD-2.json \
        --discovery data/scoring/H-SCD-discovery.json --sha <hex>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.scanner import UNIVERSE  # noqa: E402
from scripts.scanner_eval import COST_PP, MAX_ERROR_SHARE, leakage_ok, load  # noqa: E402

HORIZON_H = 24
CURVE_H = (1, 2, 4, 8, 12, 24, 48, 72)          # Part A, reported only
A1_MFE, A2_MFE = 0.25, 0.50                     # ATR24 units
COIN_FEATURES = ("confidence", "ret_24h_vs_universe_pct", "ret_7d_pct", "range_pos_24h",
                 "close_vs_ema200_pct", "funding_pct", "oi_change_24h_pct", "ls_ratio",
                 "taker_ratio", "atr_pct", "vol_ratio")
BAR_FEATURES = ("btc_ret_7d_pct", "dispersion", "session")
FEATURES = COIN_FEATURES + BAR_FEATURES
SIDES = ("LONG", "SHORT")
MIN_TERCILE_DAYS = 15
MAX_CANDIDATES = 5
ALPHA = 0.05                                    # Part B, one-sided, Holm
MIMIC_ALPHA = 0.10                              # Part C, two-sided (a 90% CI), Holm
MIMICS = {"M24": "ret_24h_vs_universe_pct", "M7": "ret_7d_pct"}
EPISODE_GAP_H = 24
MAX_NULL_SHARE = 0.20
LEVELS_SWITCH = pd.Timestamp("2026-11-19 17:00")   # first bar booked on 24h-scale levels
BLOCK, N_BOOT, SEED = 7, 5000, 2026


# ── Prices ───────────────────────────────────────────────────────────────────────────

def bars_by_coin(start, end, fetch=None, timeframe="1h", coins=UNIVERSE):
    """{coin: DataFrame(open, high, low, close)} of spot bars. 1h bars are indexed by CLOSE
    time (as scanner_eval's closes); other timeframes by OPEN time."""
    if fetch is None:
        from signals.ohlcv import fetch_ohlcv_df
        fetch = fetch_ohlcv_df
    pad = pd.Timedelta(hours=max(CURVE_H) + 2)
    since = int((start - pd.Timedelta(hours=2)).timestamp() * 1000)
    until = int((end + pad).timestamp() * 1000)
    out = {}
    for c in coins:
        df = fetch(f"{c}/USDT", timeframe, since=since, until=until)[["open", "high", "low", "close"]].copy()
        if timeframe == "1h":
            df.index = df.index + pd.Timedelta(hours=1)
        out[c] = df
    return out


# ── Tables ───────────────────────────────────────────────────────────────────────────

def _session(ts):
    return 0 if ts.hour < 8 else (1 if ts.hour < 16 else 2)


def coin_hours(cyc, bars, horizon=HORIZON_H):
    """One row per (cycle, available coin): every feature the model saw, plus r, m."""
    rows = []
    for c in cyc.itertuples():
        ctx = json.loads(c.context)["coins"]
        t, t1 = c.bar_close, c.bar_close + pd.Timedelta(hours=horizon)
        live = {k: v for k, v in ctx.items() if v}
        r24 = [v.get("ret_24h_pct") for v in live.values() if v.get("ret_24h_pct") is not None]
        btc = (ctx.get("BTC") or {}).get("ret_7d_pct")
        disp = float(np.std(r24)) if len(r24) >= 2 else None
        rets = {k: (bars[k].close[t1] / bars[k].close[t] - 1) * 100 for k in live
                if k in bars and t in bars[k].index and t1 in bars[k].index}
        m = float(np.mean(list(rets.values()))) if rets else None
        for k in ctx:
            v = ctx[k]
            row = {"cycle_id": c.id, "bar_close": t, "day": t.normalize(), "coin": k,
                   "missing": v is None, "r": rets.get(k), "m": m,
                   "btc_ret_7d_pct": btc, "dispersion": disp, "session": _session(t)}
            for f in COIN_FEATURES:
                if f != "confidence":
                    row[f] = (v or {}).get(f)
            rows.append(row)
    return pd.DataFrame(rows)


def pick_table(cyc, picks, bars, ch):
    """One row per LONG/SHORT pick: excess, net, ATR24-scaled path (MFE/MAE), features."""
    p = picks[picks.side.isin(SIDES)].merge(cyc[["id", "bar_close"]], left_on="cycle_id", right_on="id")
    feat = ch.set_index(["cycle_id", "coin"])
    rows, excluded = [], 0
    for g in p.itertuples():
        key = (g.cycle_id, g.coin)
        if key not in feat.index or pd.isna(feat.loc[key, "r"]) or pd.isna(feat.loc[key, "m"]):
            excluded += 1
            continue
        f = feat.loc[key]
        d = 1 if g.side == "LONG" else -1
        t = g.bar_close
        b = bars[g.coin]
        path = b[(b.index > t) & (b.index <= t + pd.Timedelta(hours=HORIZON_H))]
        c0 = b.close[t]
        atr24 = (np.nan if pd.isna(f["atr_pct"]) else f["atr_pct"]) * np.sqrt(HORIZON_H)
        up, dn = (path.high.max() / c0 - 1) * 100, (1 - path.low.min() / c0) * 100
        mfe, mae = (up, dn) if d == 1 else (dn, up)
        row = {"cycle_id": g.cycle_id, "bar_close": t, "day": t.normalize(), "coin": g.coin,
               "side": g.side, "d": d, "confidence": g.confidence, "r": f["r"], "m": f["m"],
               "excess": d * (f["r"] - f["m"]), "net": d * f["r"] - COST_PP,
               "mfe_atr": mfe / atr24 if atr24 > 0 else np.nan,
               "mae_atr": mae / atr24 if atr24 > 0 else np.nan}
        for k in FEATURES:
            if k != "confidence":
                row[k] = f[k]
        rows.append(row)
    return pd.DataFrame(rows), excluded


def split_days(pt):
    """(discovery days, confirmation days): the first floor(n/2) scored days, then the rest."""
    days = sorted(pt.day.unique())
    k = len(days) // 2
    return days[:k], days[k:]


# ── Bootstrap ────────────────────────────────────────────────────────────────────────

def boot_index(n, block=BLOCK, n_boot=N_BOOT, seed=SEED):
    """Moving-block bootstrap draws of positions 0..n-1, as scanner_eval.block_ci draws."""
    b = min(block, n)
    rng = np.random.default_rng(seed)
    k = -(-n // b)
    starts = rng.integers(0, n - b + 1, size=(n_boot, k))
    return (starts[:, :, None] + np.arange(b)).reshape(n_boot, -1)[:, :n]


def _nanmean_rows(x):
    with np.errstate(invalid="ignore", divide="ignore"):
        cnt = (~np.isnan(x)).sum(axis=1)
        return np.where(cnt > 0, np.nansum(x, axis=1) / np.maximum(cnt, 1), np.nan)


def holm(pvals, alpha):
    """Holm step-down: [reject?] in the order given."""
    order = np.argsort(pvals)
    reject = [False] * len(pvals)
    for rank, i in enumerate(order):
        if pvals[i] <= alpha / (len(pvals) - rank):
            reject[i] = True
        else:
            break
    return reject


# ── Part A: path anatomy ─────────────────────────────────────────────────────────────

def path_class(mfe_atr):
    if mfe_atr < A1_MFE:
        return "A1_never_right"
    return "A2_right_then_reversed" if mfe_atr >= A2_MFE else "A3_chop"


def part_a(pt, cyc, picks, bars):
    out = {}
    if len(pt):
        cls = pt.mfe_atr.map(path_class)
        for lbl, mask in (("losers", pt.net < 0), ("winners", pt.net >= 0)):
            c = cls[mask]
            out[lbl] = {"n": int(mask.sum()),
                        "shares": {k: float((c == k).mean()) if len(c) else None
                                   for k in ("A1_never_right", "A2_right_then_reversed", "A3_chop")}}
        win = pt[pt.net >= 0]
        out["winners_median_mae_atr"] = float(win.mae_atr.median()) if len(win) else None
    from scripts.scanner_eval import day_values, pick_values
    closes = {k: b.close for k, b in bars.items()}
    curve = {}
    for h in CURVE_H:
        pv = pick_values(cyc, picks, closes, h)
        curve[f"{h}h"] = float(day_values(pv).mean()) if len(pv) else None
    out["horizon_curve_day_excess_pp"] = curve
    return out


# ── Part B: conditions ───────────────────────────────────────────────────────────────

def tercile_edges(ch_disc):
    """{feature: [e1, e2]} from all coin-hours of the discovery half (session is fixed)."""
    edges = {}
    for f in FEATURES:
        if f in ("session", "confidence"):   # session buckets are fixed; confidence edges
            continue                         # come from the picks (coin-hours carry none)
        v = pd.to_numeric(ch_disc[f], errors="coerce").dropna()
        edges[f] = [float(v.quantile(1 / 3)), float(v.quantile(2 / 3))] if len(v) >= 3 else None
    return edges


def bin_of(f, v, edges):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if f == "session":
        return int(v)
    e = edges.get(f)
    if not e:
        return None
    return 0 if v < e[0] else (1 if v < e[1] else 2)


def cell_spread(pt, f, side, edges, days, idx):
    """Top-minus-bottom tercile of Claude's day excess for one (feature, side) cell."""
    p = pt[pt.side == side].copy()
    p["bin"] = [bin_of(f, v, edges) for v in p[f]]
    dpos = {d: i for i, d in enumerate(days)}
    arr = {}
    for b in (0, 2):
        dv = p[p.bin == b].groupby("day").excess.mean()
        a = np.full(len(days), np.nan)
        for d, v in dv.items():
            if d in dpos:
                a[dpos[d]] = v
        arr[b] = a
    n_top, n_bot = int((~np.isnan(arr[2])).sum()), int((~np.isnan(arr[0])).sum())
    if n_top == 0 or n_bot == 0:
        return {"spread": None, "days_top": n_top, "days_bottom": n_bot}
    spread = float(np.nanmean(arr[2]) - np.nanmean(arr[0]))
    boot = _nanmean_rows(arr[2][idx]) - _nanmean_rows(arr[0][idx])
    boot = boot[~np.isnan(boot)]
    s = np.sign(spread) or 1.0
    return {"spread": spread, "ci90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))],
            "p_one_sided": float(np.mean(s * boot <= 0)), "days_top": n_top, "days_bottom": n_bot}


def condition_effect(ch, pt, f, side, edges):
    """B per bin (the market's own d x (r - m)) and Claude's mean excess - B per bin."""
    d = 1 if side == "LONG" else -1
    u = ch[ch.r.notna()].copy()
    out = {}
    if f != "confidence":
        u["bin"] = [bin_of(f, v, edges) for v in u[f]]
        base = {b: float((d * (g.r - g.m)).mean()) for b, g in u.groupby("bin") if b is not None}
    else:
        base = {}
    p = pt[pt.side == side].copy()
    p["bin"] = [bin_of(f, v, edges) for v in p[f]]
    for b, g in p.groupby("bin"):
        if b is None:
            continue
        B = base.get(b, 0.0) if f != "confidence" else 0.0
        out[str(int(b))] = {"market_B": base.get(b), "claude_minus_B": float((g.excess - B).mean()),
                            "picks": int(len(g))}
    return out


def null_shares(ch, f):
    if f in ("confidence", "session") or f not in ch:
        return 0.0
    live = ch[~ch.missing]
    return float(live[f].isna().mean()) if len(live) else 0.0


def part_b_discovery(pt, ch, days):
    disc_pt, disc_ch = pt[pt.day.isin(days)], ch[ch.day.isin(days)]
    edges = tercile_edges(disc_ch)
    conf = pd.to_numeric(disc_pt.confidence, errors="coerce").dropna()
    edges["confidence"] = [float(conf.quantile(1 / 3)), float(conf.quantile(2 / 3))] if len(conf) >= 3 else None
    idx = boot_index(len(days))
    cells, eligible = [], []
    for f in FEATURES:
        too_null = null_shares(disc_ch, f) > MAX_NULL_SHARE
        for side in SIDES:
            c = {"feature": f, "side": side, **cell_spread(disc_pt, f, side, edges, days, idx),
                 "conditions": condition_effect(disc_ch, disc_pt, f, side, edges), "too_null": too_null}
            cells.append(c)
            if (not too_null and c["spread"] is not None and c["days_top"] >= MIN_TERCILE_DAYS
                    and c["days_bottom"] >= MIN_TERCILE_DAYS and (c["ci90"][0] > 0 or c["ci90"][1] < 0)):
                hw = (c["ci90"][1] - c["ci90"][0]) / 2
                eligible.append((abs(c["spread"]) / hw if hw > 0 else np.inf, c))
    eligible.sort(key=lambda x: -x[0])
    cands = [{"feature": c["feature"], "side": c["side"], "sign": int(np.sign(c["spread"])),
              "spread": c["spread"], "ci90": c["ci90"]} for _, c in eligible[:MAX_CANDIDATES]]
    return {"edges": edges, "cells": cells, "candidates": cands}


def part_b_confirm(pt, ch, days, disc):
    conf_pt = pt[pt.day.isin(days)]
    idx = boot_index(len(days))
    rows = []
    for c in disc["candidates"]:
        r = cell_spread(conf_pt, c["feature"], c["side"], disc["edges"], days, idx)
        enough = r["days_top"] >= MIN_TERCILE_DAYS and r["days_bottom"] >= MIN_TERCILE_DAYS
        same = r["spread"] is not None and np.sign(r["spread"]) == c["sign"]
        p = (r["p_one_sided"] if same else 1.0) if r["spread"] is not None else 1.0
        rows.append({**c, "confirm": r, "enough_days": bool(enough), "p": p})
    rej = holm([x["p"] for x in rows], ALPHA) if rows else []
    for x, ok in zip(rows, rej):
        x["verdict"] = ("INCONCLUSIVE" if not x["enough_days"] else
                        "CONFIRMED" if ok else "NOT CONFIRMED")
    return rows


# ── Part C: momentum mimics ──────────────────────────────────────────────────────────

def mimic_picks(cyc, picks, feature):
    """Per cycle: as many LONGs / SHORTs as Claude made, on the top / bottom coins by
    `feature` from the stored context. Ties break by UNIVERSE order; missing coins skipped."""
    rows = []
    act = picks[picks.side.isin(SIDES)]
    for c in cyc.itertuples():
        mine = act[act.cycle_id == c.id]
        kl, ks = int((mine.side == "LONG").sum()), int((mine.side == "SHORT").sum())
        if kl + ks == 0:
            continue
        ctx = json.loads(c.context)["coins"]
        vals = [(k, v[feature]) for k, v in ctx.items() if v and v.get(feature) is not None]
        order = sorted(vals, key=lambda kv: (-kv[1], UNIVERSE.index(kv[0]) if kv[0] in UNIVERSE else 99))
        longs = [k for k, _ in order[:kl]]
        shorts = [k for k, _ in reversed(order) if k not in longs][:ks]
        rows += [{"cycle_id": c.id, "coin": k, "side": "LONG", "confidence": None} for k in longs]
        rows += [{"cycle_id": c.id, "coin": k, "side": "SHORT", "confidence": None} for k in shorts]
    return pd.DataFrame(rows, columns=["cycle_id", "coin", "side", "confidence"])


def part_c(cyc, picks, bars):
    from scripts.scanner_eval import day_values, pick_values
    closes = {k: b.close for k, b in bars.items()}
    mine = pick_values(cyc, picks, closes, HORIZON_H)
    dm = day_values(mine) if len(mine) else pd.Series(dtype=float)
    act = picks[picks.side.isin(SIDES)]
    claude = set(zip(act.cycle_id, act.coin, act.side))
    res, pv = {}, []
    for name, feat in MIMICS.items():
        mp = mimic_picks(cyc, picks, feat)
        agree = len(claude & set(zip(mp.cycle_id, mp.coin, mp.side))) / len(claude) if claude else None
        pvm = pick_values(cyc, mp, closes, HORIZON_H) if len(mp) else pd.DataFrame()
        dmm = day_values(pvm) if len(pvm) else pd.Series(dtype=float)
        both = dm.index.intersection(dmm.index)
        diff = (dm[both] - dmm[both]).sort_index().values
        r = {"agreement": agree, "mimic_mean_day_excess_pp": float(dmm.mean()) if len(dmm) else None,
             "paired_days": int(len(diff))}
        if len(diff):
            boot = diff[boot_index(len(diff))].mean(axis=1)
            mean = float(diff.mean())
            r.update(mean_diff_pp=mean, ci90=[float(np.percentile(boot, 5)), float(np.percentile(boot, 95))],
                     p_two_sided=float(min(1.0, 2 * min(np.mean(boot <= 0), np.mean(boot >= 0)))))
        res[name] = r
        pv.append(r.get("p_two_sided", 1.0))
    rej = holm(pv, MIMIC_ALPHA)
    for (name, r), ok in zip(res.items(), rej):
        m = r.get("mean_diff_pp")
        if m is None:
            r["reading"] = "no paired days"
        elif ok and m < 0:
            r["reading"] = "the rule beats Claude"
        elif ok and m > 0:
            r["reading"] = "Claude adds to momentum"
        elif (r["agreement"] or 0) >= 0.70:
            r["reading"] = "Claude ≈ momentum rule"
        else:
            r["reading"] = "diff includes 0, agreement < 70%"
    return res


# ── Part D: episodes ─────────────────────────────────────────────────────────────────

def episodes(pt):
    """Episode id per pick: same coin and side, each pick within EPISODE_GAP_H of the last."""
    p = pt.sort_values(["coin", "side", "bar_close"]).copy()
    eid, last, n = [], None, -1
    for r in p.itertuples():
        if last is None or (r.coin, r.side) != last[0] or \
                r.bar_close - last[1] > pd.Timedelta(hours=EPISODE_GAP_H):
            n += 1
        eid.append(n)
        last = ((r.coin, r.side), r.bar_close)
    p["episode"] = eid
    return p


def part_d(pt):
    if not len(pt):
        return {"episodes": 0}
    p = episodes(pt)
    first = p.sort_values("bar_close").groupby("episode").first()
    x = first.excess.values
    rng = np.random.default_rng(SEED)
    boot = x[rng.integers(0, len(x), size=(N_BOOT, len(x)))].mean(axis=1)
    loo = [float(np.delete(x, i).mean()) for i in range(len(x))] if len(x) > 1 else []
    top3 = pt.coin.value_counts().head(3)
    return {"picks": int(len(pt)), "episodes": int(len(first)),
            "median_picks_per_episode": float(p.groupby("episode").size().median()),
            "top3_coins": {k: int(v) for k, v in top3.items()},
            "top3_share": float(top3.sum() / len(pt)),
            "episode_mean_excess_pp": float(x.mean()),
            "episode_ci90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))],
            "leave_one_episode_out": [min(loo), max(loo)] if loo else None}


# ── Part E: execution gaps ───────────────────────────────────────────────────────────

def part_e(pt, perp_close, bars5, book):
    """5-minute fill, spot vs perp close, and the books against the picks' own net."""
    out = {}
    delay, gap = [], []
    for r in pt.itertuples():
        b5 = bars5.get(r.coin)
        if b5 is None:
            continue
        # 5m bars are indexed by OPEN time: the one opening at t carries the price at t (the
        # 1h bar's close), the one opening at t + 5 min the price the scanner answers at.
        spot_t = b5.open.get(r.bar_close)
        spot_5 = b5.open.get(r.bar_close + pd.Timedelta(minutes=5))
        if spot_t and spot_5:
            delay.append(r.d * (spot_5 / spot_t - 1) * 100)
        pc = perp_close.get((r.cycle_id, r.coin))
        if pc and spot_t:
            gap.append((pc / spot_t - 1) * 100)
    out["five_minute_fill_pp"] = ({"n": len(delay), "mean": float(np.mean(delay)),
                                   "p10": float(np.percentile(delay, 10)),
                                   "p90": float(np.percentile(delay, 90))} if delay else None)
    out["perp_minus_spot_pp"] = ({"n": len(gap), "mean": float(np.mean(gap)),
                                  "mean_abs": float(np.mean(np.abs(gap)))} if gap else None)
    books = {}
    if book is not None and len(book):
        net = pt.set_index(["cycle_id", "coin"]).net
        for (bk, after), g in book.groupby(["book", book.opened_bar >= LEVELS_SWITCH]):
            pick_net = [net.get((a, b)) for a, b in zip(g.cycle_id, g.coin)]
            pick_net = [x for x in pick_net if x is not None and not pd.isna(x)]
            books[f"{bk}_{'24h_levels' if after else '1h_levels'}"] = {
                "closed": int(len(g)), "mean_book_pnl_pct": float(g.pnl_pct.mean()),
                "mean_pick_net_pp": float(np.mean(pick_net)) if pick_net else None}
    out["books_vs_picks"] = books
    return out


# ── Part F: data health ──────────────────────────────────────────────────────────────

def part_f(ch, cyc_all, excluded, halves):
    live = ch[~ch.missing]
    out = {"missing_coin_hours": {k: int(g.missing.sum()) for k, g in ch.groupby("coin")},
           "error_share": float(cyc_all.error.notna().mean()) if len(cyc_all) else 0.0,
           "excluded_picks": int(excluded), "null_share": {}}
    for name, days in halves.items():
        part = ch[ch.day.isin(days)]
        out["null_share"][name] = {f: null_shares(part, f) for f in FEATURES}
    out["coin_hours"] = int(len(live))
    return out


# ── Run ──────────────────────────────────────────────────────────────────────────────

def window(db, model=None):
    """H-SC's window: v2, error-free, one model (the most frequent unless given)."""
    cyc, picks = load(db)
    ok = cyc[cyc.error.isna()]
    if model is None and len(ok):
        model = ok.model.value_counts().idxmax()
    ok = ok[ok.model == model] if model is not None else ok
    return cyc, ok.reset_index(drop=True), picks[picks.cycle_id.isin(ok.id)], model


def _perp_closes(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {(a, b): c for a, b, c in con.execute("SELECT cycle_id, coin, close FROM scan_picks")}
    finally:
        con.close()


def _book(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        b = pd.read_sql("SELECT book, coin, cycle_id, opened_bar, pnl_pct FROM scan_positions "
                        "WHERE closed_bar IS NOT NULL", con)
    finally:
        con.close()
    b["opened_bar"] = pd.to_datetime(b.opened_bar, utc=True).dt.tz_localize(None)
    return b


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(phase, arms, fetch=None, discovery_path=None, sha=None):
    """arms = {"pure": db, "hybrid": db, "random": db}; pure is required and sets the window."""
    cyc_all, cyc, picks, model = window(arms["pure"])
    if cyc.empty:
        return {"error": "no scored cycles"}
    bad = leakage_ok(cyc)
    if bad:
        return {"error": f"leakage check failed for {len(bad)} cycles: discarded", "cycles": bad[:10]}
    start, end = cyc.bar_close.min(), cyc.bar_close.max()
    coins = sorted({k for x in cyc.context for k in json.loads(x)["coins"]},
                   key=lambda k: UNIVERSE.index(k) if k in UNIVERSE else 99)
    bars = bars_by_coin(start, end, fetch, coins=coins)
    ch = coin_hours(cyc, bars)
    pt, excluded = pick_table(cyc, picks, bars, ch)
    disc_days, conf_days = split_days(pt)
    res = {"phase": phase, "model": model, "window": [str(start), str(end)],
           "days": {"discovery": len(disc_days), "confirmation": len(conf_days)},
           "split_after": str(disc_days[-1]) if disc_days else None}
    healthy = (float(cyc_all.error.notna().mean()) if len(cyc_all) else 0.0) <= MAX_ERROR_SHARE
    res["healthy"] = healthy
    if phase == "confirm":
        if not discovery_path or not Path(discovery_path).exists():
            return {**res, "error": "no discovery file: confirm refused"}
        if sha is None or sha256(discovery_path) != sha:
            return {**res, "error": "discovery file hash does not match: confirm refused"}
        disc = json.loads(Path(discovery_path).read_text())
        if disc.get("split_after") != res["split_after"]:
            return {**res, "error": "split differs from discovery: confirm refused"}
        res["part_b_confirm"] = part_b_confirm(pt, ch, conf_days, disc)
        if not healthy:
            for x in res["part_b_confirm"]:
                x["verdict"] = "INCONCLUSIVE"
        return res
    res["part_a"] = {"pure": part_a(pt, cyc, picks, bars)}
    res["part_b_discovery"] = part_b_discovery(pt, ch, disc_days)
    res["part_c"] = part_c(cyc, picks, bars)
    res["part_d"] = {"pure": part_d(pt)}
    bars5 = bars_by_coin(start, end, fetch, timeframe="5m", coins=coins)
    res["part_e"] = {"pure": part_e(pt, _perp_closes(arms["pure"]), bars5, _book(arms["pure"]))}
    res["part_f"] = part_f(ch, cyc_all, excluded, {"discovery": disc_days, "confirmation": conf_days})
    for arm in ("hybrid", "random"):
        db = arms.get(arm)
        if not db or not Path(db).exists():
            continue
        a_all, a_cyc, a_picks, _ = window(db)
        a_cyc = a_cyc[(a_cyc.bar_close >= start) & (a_cyc.bar_close <= end)]
        a_picks = a_picks[a_picks.cycle_id.isin(a_cyc.id)]
        a_ch = coin_hours(a_cyc, bars)
        a_pt, _ = pick_table(a_cyc, a_picks, bars, a_ch)
        res["part_a"][arm] = part_a(a_pt, a_cyc, a_picks, bars)
        res["part_d"][arm] = part_d(a_pt)
        res["part_e"][arm] = part_e(a_pt, _perp_closes(db), bars5, _book(db))
        edges = res["part_b_discovery"]["edges"]
        res.setdefault("part_b_arms", {})[arm] = [
            {"feature": f, "side": s, **cell_spread(a_pt[a_pt.day.isin(disc_days)], f, s, edges,
                                                     disc_days, boot_index(max(len(disc_days), 1)))}
            for f in FEATURES for s in SIDES]
    if discovery_path:
        body = {"split_after": res["split_after"], "edges": res["part_b_discovery"]["edges"],
                "candidates": res["part_b_discovery"]["candidates"], "model": model,
                "window": res["window"]}
        Path(discovery_path).write_text(json.dumps(body, indent=1, default=str))
        res["discovery_sha256"] = sha256(discovery_path)
    return res


def _summary(res):
    if "error" in res:
        return f"H-SCD {res.get('phase')}: {res['error']}"
    lines = [f"H-SCD {res['phase']}  model {res['model']}  window {res['window'][0]} .. {res['window'][1]}",
             f"days: discovery {res['days']['discovery']}, confirmation {res['days']['confirmation']}"
             f"  healthy {res['healthy']}"]
    if res["phase"] == "confirm":
        for x in res["part_b_confirm"]:
            lines.append(f"  B {x['feature']} {x['side']} sign {x['sign']:+d}: {x['verdict']}  p {x['p']:.3f}")
        if not res["part_b_confirm"]:
            lines.append("  B: no candidate from discovery, nothing to confirm")
        return "\n".join(lines)
    a = res["part_a"]["pure"]
    lines.append(f"A losers {a.get('losers')}  winners median MAE {a.get('winners_median_mae_atr')}")
    lines.append(f"A curve {a['horizon_curve_day_excess_pp']}")
    lines.append(f"B candidates: {[(c['feature'], c['side'], c['sign']) for c in res['part_b_discovery']['candidates']]}")
    for k, r in res["part_c"].items():
        lines.append(f"C {k}: agreement {r['agreement']}  diff {r.get('mean_diff_pp')}  {r['reading']}")
    d = res["part_d"]["pure"]
    lines.append(f"D episodes {d.get('episodes')} of {d.get('picks')} picks, top3 share {d.get('top3_share')}")
    lines.append(f"E {res['part_e']['pure']}")
    lines.append(f"F error share {res['part_f']['error_share']:.1%}, excluded {res['part_f']['excluded_picks']}")
    if "discovery_sha256" in res:
        lines.append(f"discovery sha256 {res['discovery_sha256']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("discovery", "confirm"), required=True)
    ap.add_argument("--db", default="data/scanner.db")
    ap.add_argument("--hybrid-db", default="data/scanner_hybrid.db")
    ap.add_argument("--random-db", default="data/scanner_random.db")
    ap.add_argument("--discovery", required=True, help="discovery file (written, or read to confirm)")
    ap.add_argument("--sha", help="confirm: the discovery file's SHA-256 as recorded at discovery")
    ap.add_argument("--sha-file", help="confirm: read --sha from this file")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.phase == "discovery" and Path(args.discovery).exists():
        print("discovery file already exists: discovery runs once")
        return 3
    sha = args.sha or (Path(args.sha_file).read_text().strip() if args.sha_file and Path(args.sha_file).exists() else None)
    res = run(args.phase, {"pure": args.db, "hybrid": args.hybrid_db, "random": args.random_db},
              discovery_path=args.discovery, sha=sha)
    print(_summary(res))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, default=str))
    if args.phase == "discovery" and "discovery_sha256" in res:
        Path(args.discovery).with_suffix(".sha256").write_text(res["discovery_sha256"] + "\n")
    return 1 if "error" in res else 0


if __name__ == "__main__":
    sys.exit(main())
