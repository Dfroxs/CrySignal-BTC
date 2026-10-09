#!/usr/bin/env python3
"""Score H-D exactly as pre-registered — `docs/superpowers/specs/2026-10-10-derivs-archive-prereg.md`.

Do Binance's 30-day derivatives stats (archived by `archive_binance_derivs.py`) predict
the forward 24h perp return? Spearman IC per symbol, pooled as the mean over the alts,
with a 90% block bootstrap whose 24h blocks are the SAME calendar hours for every symbol.

    # on the VPS (Binance is reachable there), on or after 2026-11-08:
    ./venv/bin/python scripts/hd_eval.py --derivs data/derivs --fetch-klines
    ./venv/bin/python scripts/hd_eval.py --derivs data/derivs --out data/derivs/hd_result.json

`--fetch-klines` only downloads perp 1h klines into `<derivs>/klines/`; scoring can then
run anywhere on a copy of the folder. Scoring refuses to run before SCORE_FROM.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ALTS = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
BTC = "BTCUSDT"
BTC_FROM = pd.Timestamp("2026-10-09 12:34:20")          # run 3's started_at; earlier BTC rows are in-design
SCORE_FROM = pd.Timestamp("2026-11-08")                  # no figure before this (prereg § Discipline)
RETURN_BY = pd.Timestamp("2026-11-08")                   # forward return must complete by then
FIELDS = {                                               # stat -> (file stem, column, transform)
    "ls_global": ("ls_global", "longShortRatio", "level"),
    "ls_top_position": ("ls_top_position", "longShortRatio", "level"),
    "oi_change_24h": ("oi", "sumOpenInterest", "pct24"),
    "ls_top_account": ("ls_top_account", "longShortRatio", "level"),
    "taker": ("taker", "buySellRatio", "level"),
}
PRIMARY, SCREENED = "ls_global", ("ls_top_position", "oi_change_24h")
HORIZONS = (24, 4, 12)                                   # 24h decides; 4h and 12h are descriptive
MIN_OBS, MIN_ALTS = 500, 4
BLOCK, N_BOOT, SEED = 24, 2000, 2026
H = pd.Timedelta(hours=1)


# ---------------------------------------------------------------- data

def fetch_klines(derivs: Path, symbols) -> None:
    """Perp 1h klines covering the archive, written to <derivs>/klines/<SYMBOL>.csv."""
    out = derivs / "klines"
    out.mkdir(parents=True, exist_ok=True)
    with requests.Session() as s:
        for sym in symbols:
            first = pd.read_csv(derivs / f"{sym}_ls_global.csv")["timestamp"].min()
            start, rows = int(first) - 48 * 3_600_000, []
            while True:
                r = s.get("https://fapi.binance.com/fapi/v1/klines",
                          params={"symbol": sym, "interval": "1h", "startTime": start, "limit": 1500},
                          timeout=30, headers={"User-Agent": "curl/8.4"})
                r.raise_for_status()
                batch = [b for b in r.json() if int(b[6]) < time.time() * 1000]   # closed bars
                if not batch:
                    break
                rows += batch
                start = int(batch[-1][0]) + 3_600_000
                time.sleep(0.2)
            with open(out / f"{sym}.csv", "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["open_time", "open", "close"])
                w.writerows([b[0], b[1], b[4]] for b in rows)
            print(f"  klines {sym}: {len(rows)}")


def _ts(ms):
    return pd.to_datetime(ms.astype("int64"), unit="ms")


def load_symbol(derivs: Path, sym: str) -> pd.DataFrame:
    """Hourly frame indexed by stat stamp T: each field, and forward returns from the
    open of the kline starting at T+1h to the close of the kline starting at T+h."""
    k = pd.read_csv(derivs / "klines" / f"{sym}.csv")
    k.index = _ts(k["open_time"])
    k = k[~k.index.duplicated()].asfreq("h")
    out = {}
    for name, (stem, col, how) in FIELDS.items():
        f = pd.read_csv(derivs / f"{sym}_{stem}.csv")
        s = pd.Series(f[col].astype(float).values, index=_ts(f["timestamp"]))
        s = s[~s.index.duplicated()].asfreq("h")
        out[name] = s / s.shift(24) - 1 if how == "pct24" else s
    df = pd.DataFrame(out)
    entry = k["open"].reindex(df.index + H).values
    for h in HORIZONS:
        exit_ = k["close"].reindex(df.index + h * H).values
        df[f"fwd_{h}h"] = exit_ / entry - 1
    df["_last_bar"] = df.index + 24 * H           # the 24h return's last kline opens here
    return df


def scored_rows(df: pd.DataFrame, sym: str) -> pd.DataFrame:
    """The pre-registered window: alts end where the 24h return completes by RETURN_BY;
    BTC also starts at run 3's start."""
    m = (df["_last_bar"] + H) <= RETURN_BY
    if sym == BTC:
        m &= df.index >= BTC_FROM
    out = df[m]
    if sym == BTC:
        assert out.index.min() >= BTC_FROM, "BTC row before run 3's start in a scored set — discard"
    return out


# ---------------------------------------------------------------- statistics

def _ic(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return float("nan"), int(m.sum())
    return float(pd.Series(x[m]).rank().corr(pd.Series(y[m]).rank())), int(m.sum())


def pooled_ic(frames: dict, field: str, horizon: int = 24, n_boot: int = N_BOOT, seed: int = SEED):
    """Per-symbol ICs (symbols with ≥ MIN_OBS pairs), their mean, and a 90% interval from
    moving 24h blocks drawn on ONE hourly calendar shared by every symbol."""
    y = f"fwd_{horizon}h"
    per, counted = {}, []
    for sym, df in frames.items():
        ic, n = _ic(df[field].to_numpy(float), df[y].to_numpy(float))
        per[sym] = {"ic": ic, "n": n, "counted": n >= MIN_OBS}
        if n >= MIN_OBS:
            counted.append(sym)
    if not counted:
        return {"per_symbol": per, "counted": [], "pooled": float("nan"), "ci": [float("nan")] * 2}
    cal = pd.date_range(min(frames[s].index.min() for s in counted),
                        max(frames[s].index.max() for s in counted), freq="h")
    pos = {s: cal.get_indexer(frames[s].index) for s in counted}
    rng = np.random.default_rng(seed)
    n_blocks = max(len(cal) // BLOCK, 1)
    boot = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(len(cal) - BLOCK, 1), n_blocks)
        hours = np.concatenate([np.arange(s, s + BLOCK) for s in starts])
        ics = []
        for s in counted:
            df = frames[s]
            row_of = pd.Series(np.arange(len(df)), index=pos[s])
            rows = row_of.reindex(hours).dropna().astype(int).to_numpy()
            ics.append(_ic(df[field].to_numpy(float)[rows], df[y].to_numpy(float)[rows])[0])
        boot.append(np.nanmean(ics))
    pooled = float(np.mean([per[s]["ic"] for s in counted]))
    return {"per_symbol": per, "counted": counted, "pooled": pooled,
            "ci": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]}


def verdict_hd1(r: dict) -> str:
    if len(r["counted"]) < MIN_ALTS:
        return "INCONCLUSIVE"
    lo, hi = r["ci"]
    if r["pooled"] <= 0 or hi < 0:
        return "FAIL"
    positive = sum(1 for s in r["counted"] if r["per_symbol"][s]["ic"] > 0)
    return "PASS" if lo > 0 and positive >= 4 else "INCONCLUSIVE"


def screen(r: dict) -> str:
    if len(r["counted"]) < MIN_ALTS:
        return "INCONCLUSIVE"
    lo, hi = r["ci"]
    sign = 1 if r["pooled"] > 0 else -1
    agree = sum(1 for s in r["counted"] if np.sign(r["per_symbol"][s]["ic"]) == sign)
    if (lo > 0 or hi < 0) and agree >= 4:
        return f"LEAD ({'+' if sign > 0 else '−'})"
    return "no lead"


def gaps(derivs: Path, sym: str) -> int:
    t = pd.read_csv(derivs / f"{sym}_ls_global.csv")["timestamp"].astype("int64").sort_values()
    return int((t.diff().dropna() != 3_600_000).sum())


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derivs", type=Path, required=True)
    ap.add_argument("--fetch-klines", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.fetch_klines:
        fetch_klines(args.derivs, ALTS + (BTC,))
        return 0
    if pd.Timestamp.now("UTC").tz_localize(None) < SCORE_FROM:
        print(f"refused: H-D may not be scored before {SCORE_FROM.date()} (prereg § Discipline)")
        return 2

    alts = {s: scored_rows(load_symbol(args.derivs, s), s) for s in ALTS}
    btc = {BTC: scored_rows(load_symbol(args.derivs, BTC), BTC)}
    res = {"rows": {s: len(d) for s, d in {**alts, **btc}.items()},
           "gaps": {s: gaps(args.derivs, s) for s in ALTS + (BTC,)}, "fields": {}}
    print(f"H-D  rows {res['rows']}  gaps {res['gaps']}\n")
    for f in FIELDS:
        r = pooled_ic(alts, f)
        role = "H-D1" if f == PRIMARY else ("screen" if f in SCREENED else "descriptive")
        v = verdict_hd1(r) if f == PRIMARY else (screen(r) if f in SCREENED else "—")
        desc = {f"pooled_{h}h": pooled_ic(alts, f, h, n_boot=200)["pooled"] for h in (4, 12)}
        b_ic, b_n = _ic(btc[BTC][f].to_numpy(float), btc[BTC]["fwd_24h"].to_numpy(float))
        res["fields"][f] = {"role": role, "verdict": v, **r, **desc,
                            "btc_run3": {"ic": b_ic, "n": b_n}}
        per = "  ".join(f"{s[:-4]} {r['per_symbol'][s]['ic']:+.3f}" for s in ALTS)
        print(f"{f:16s} {role:11s} pooled {r['pooled']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]  "
              f"→ {v}\n  {per}   BTC(run 3) {b_ic:+.3f} n={b_n}   4h {desc['pooled_4h']:+.3f} "
              f"12h {desc['pooled_12h']:+.3f}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
