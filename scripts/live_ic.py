#!/usr/bin/env python3
"""Score run 3's H-L and H-V1 — `docs/superpowers/specs/2026-10-09-run3-prereg.md`.

H-L   do basis_pct and ls_ratio predict the forward 24h BTC return?
H-V1  does each variant's net score (buy − sell) predict it better than base's?

Both are measured per hourly FUTURES cycle, because the trade count never has the power
to answer them. ICs are Spearman, with intervals from a moving-block bootstrap (24h
blocks), since hourly observations are not independent. The H-V1 difference resamples
the SAME cycles for both scores, so it is a paired comparison.

Run on a pulled copy of the run's database, never the live file, and only once the
pre-registration allows it (day 30 or later):
    ./venv/bin/python scripts/live_ic.py --db data/server.db --start <run-3 started_at>

Every other field is printed too, as a DESCRIPTIVE table that is never a criterion.
That is the weekly edge scan.
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

HORIZON = 24
BLOCK = 24
N_BOOT = 2000
MIN_OBS = 500
SEED = 2026

FIELDS = ["funding_rate", "ls_ratio", "oi_change", "basis_pct", "taker_ratio", "fear_greed",
          "dxy_change", "sp500_change", "vix_change", "gold_change", "btc_dom", "stablecoin_b"]
# What a fetch failure leaves behind — not a reading (as history.get_market_history).
PLACEHOLDERS = {"funding_rate": (0.0,), "ls_ratio": (1.0, 0.0), "basis_pct": (0.0,)}
BOOKS = ("base", "rel_engine_dir", "rel_ic_dir")


def norm_ts(ts):
    """'YYYY-MM-DD HH:MM:SS' in UTC, from any form the run's tables or manifest carry.

    cycle_log and signal_blocks store '2026-10-09 17:01:03'; paper_positions and the
    shadow tables store '2026-10-09T16:01:03.213149+00:00'. Compared as raw strings, ' '
    sorts before 'T', so a start in one form silently drops or admits a whole day of the
    other. Every --start filter compares ts_sql(column) >= norm_ts(start).
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def ts_sql(col):
    return f"substr(replace({col}, 'T', ' '), 1, 19)"


def load_cycles(db, start=None):
    """Hourly futures frame: fields (placeholders → NaN), net score per book, and the
    forward 24h return. Missing hours stay as empty rows, so a shift never pairs a cycle
    with the wrong price."""
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(cycle_log)")}
    # A run-1/2 database predates the column: read it as all-NULL rather than fail.
    variants = "variants" if "variants" in cols else "NULL AS variants"
    q = (f"SELECT timestamp, price, {variants}, {', '.join(FIELDS)} "
         f"FROM cycle_log WHERE mode='futures'")
    params = []
    if start:
        q += f" AND {ts_sql('timestamp')} >= ?"
        params.append(norm_ts(start))
    df = pd.read_sql(q + " ORDER BY timestamp", con, params=params)
    ts = pd.to_datetime(df["timestamp"].str.replace("T", " ").str[:19])
    df.index = ts.dt.floor("h")
    df = df[~df.index.duplicated(keep="first")]
    for col, bad in PLACEHOLDERS.items():
        df.loc[df[col].isin(bad), col] = np.nan
    for b in BOOKS:
        df[f"net_{b}"] = [_net(v, b) for v in df["variants"]]
    df = df.drop(columns=["timestamp", "variants"]).asfreq("h")
    df["fwd_24h"] = df["price"].shift(-HORIZON) / df["price"] - 1
    return df


def _net(raw, book):
    if not raw:
        return np.nan
    s = json.loads(raw).get(book)
    if not s or s.get("buy_score") is None:
        return np.nan
    return float(s["buy_score"]) - float(s.get("sell_score") or 0)


def _ic(x, y):
    return float(np.corrcoef(pd.Series(x).rank(), pd.Series(y).rank())[0, 1])


def _blocks(n, rng):
    starts = rng.integers(0, max(n - BLOCK, 1), max(n // BLOCK, 1))
    return np.concatenate([np.arange(s, min(s + BLOCK, n)) for s in starts])


def block_ic(x, y, n_boot=N_BOOT, seed=SEED):
    """(IC, 5th pct, 95th pct, n) on rows where both are present."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    if len(x) < 3:
        return float("nan"), float("nan"), float("nan"), len(x)
    rng = np.random.default_rng(seed)
    boot = [_ic(x[s], y[s]) for s in (_blocks(len(x), rng) for _ in range(n_boot))]
    return _ic(x, y), float(np.percentile(boot, 5)), float(np.percentile(boot, 95)), len(x)


def paired_ic_diff(a, b, y, n_boot=N_BOOT, seed=SEED):
    """(IC(a) − IC(b), 5th, 95th, n), both ICs on the same resampled rows."""
    a, b, y = (np.asarray(v, float) for v in (a, b, y))
    m = ~(np.isnan(a) | np.isnan(b) | np.isnan(y))
    a, b, y = a[m], b[m], y[m]
    if len(y) < 3:
        return float("nan"), float("nan"), float("nan"), len(y)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(n_boot):
        s = _blocks(len(y), rng)
        boot.append(_ic(a[s], y[s]) - _ic(b[s], y[s]))
    return (_ic(a, y) - _ic(b, y), float(np.percentile(boot, 5)),
            float(np.percentile(boot, 95)), len(y))


def verdict(est, lo, hi, n, min_n=MIN_OBS):
    if n < min_n or any(v != v for v in (est, lo, hi)):
        return "INCONCLUSIVE"
    if est > 0 and lo > 0:
        return "PASS"
    if hi < 0:
        return "FAIL"
    return "INCONCLUSIVE"


def health(df):
    """The pre-registration's discard conditions, over cycles that exist."""
    live = df[df["price"].notna()]
    null_v = float(live["net_base"].isna().mean()) if len(live) else 1.0
    fund0 = float(live["funding_rate"].isna().mean()) if len(live) else 1.0
    return {"cycles": int(len(live)), "variants_null_share": round(null_v, 4),
            "funding_placeholder_share": round(fund0, 4),
            "discard": null_v > 0.05 or fund0 > 0.10}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", default=None, help="run's started_at; default: all rows")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    df = load_cycles(args.db, args.start)
    hl = health(df)
    y = df["fwd_24h"].values
    print(f"{args.db}  futures  {df.index[0]} → {df.index[-1]}  cycles={hl['cycles']}")
    print(f"health: variants NULL {hl['variants_null_share']:.1%} (≤5%), "
          f"funding placeholder {hl['funding_placeholder_share']:.1%} (≤10%)"
          f"{'   ⚠️ DISCARD per prereg' if hl['discard'] else ''}\n")

    result = {"health": hl, "H-L": {}, "H-V1": {}, "descriptive": {}}
    print("H-L  field IC vs fwd 24h")
    for key, col in (("H-L1", "basis_pct"), ("H-L2", "ls_ratio")):
        ic, lo, hi, n = block_ic(df[col].values, y)
        v = verdict(ic, lo, hi, n)
        result["H-L"][key] = {"field": col, "ic": ic, "ci": [lo, hi], "n": n, "verdict": v}
        print(f"  {key} {col:10s} IC={ic:+.3f}  [{lo:+.3f}, {hi:+.3f}]  n={n}  → {v}")

    print("\nH-V1 IC(variant net) − IC(base net), paired")
    for b in BOOKS[1:]:
        d, lo, hi, n = paired_ic_diff(df[f"net_{b}"].values, df["net_base"].values, y)
        v = verdict(d, lo, hi, n)
        result["H-V1"][b] = {"diff": d, "ci": [lo, hi], "n": n, "verdict": v}
        print(f"  {b:15s} Δ={d:+.3f}  [{lo:+.3f}, {hi:+.3f}]  n={n}  → {v}")

    print("\ndescriptive (never a criterion): every field and book, IC vs fwd 24h")
    for col in FIELDS + [f"net_{b}" for b in BOOKS]:
        ic, lo, hi, n = block_ic(df[col].values, y)
        result["descriptive"][col] = {"ic": ic, "ci": [lo, hi], "n": n}
        print(f"  {col:20s} IC={ic:+.3f}  [{lo:+.3f}, {hi:+.3f}]  n={n}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
