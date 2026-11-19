#!/usr/bin/env python3
"""Score H-FG exactly as pre-registered — `docs/superpowers/specs/2026-11-19-macro-fng-prereg.md`.

Does Fear & Greed point the way the live overlay assumes, with extreme fear before rises and
greed before falls? Daily index value at day D against BTC's return from D's open to D+2's
close (72 h). A bucket's excess is its mean forward return minus the all-days mean. The 90% CI
is a moving-block bootstrap of days. Both halves of the window must agree for a PASS.

    ./venv/bin/python scripts/fng_ic.py --smoke     # day counts per bucket, no returns
    ./venv/bin/python scripts/fng_ic.py --out docs/superpowers/specs/2026-11-19-macro-fng-run/hfg.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "macro_hist"
START, END = pd.Timestamp("2018-02-01"), pd.Timestamp("2026-11-15")
HALF = pd.Timestamp("2022-07-01")                 # second half starts here
BUCKETS = {"FEAR": lambda v: v <= 20, "FEAR30": lambda v: v <= 30,
           "GREED": lambda v: v >= 70, "XGREED": lambda v: v >= 80}
PRIMARY = {"H-FG1": ("FEAR", +1), "H-FG2": ("GREED", -1)}   # bucket, sign the overlay assumes
BLOCK, N_BOOT, SEED, MIN_DAYS = 10, 5000, 2026, 100


def load_fng(cache_dir=HIST):
    """Daily index {date: value}, fetched once and saved with its download date."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    saved = sorted(cache_dir.glob("fng_*.json"))
    if saved:
        data = json.loads(saved[-1].read_text())
    else:
        r = requests.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        (cache_dir / f"fng_{datetime.now(UTC):%Y%m%d}.json").write_text(json.dumps(data))
    s = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="s").normalize(): int(x["value"]) for x in data})
    return s.sort_index()


def load_btc_daily():
    from signals.ohlcv import fetch_ohlcv_df
    since = int((START - pd.Timedelta(days=2)).timestamp() * 1000)
    until = int((END + pd.Timedelta(days=4)).timestamp() * 1000)
    df = fetch_ohlcv_df("BTC/USDT", "1d", since=since, until=until)
    return df[["open", "close"]]


def frame(fng, px, horizon_days=3):
    """One row per day D in the window: F&G at D and BTC's return from D's open to the
    close of day D + horizon_days - 1."""
    px = px[~px.index.duplicated()]
    idx = px.index.normalize()
    open_, close = pd.Series(px["open"].values, index=idx), pd.Series(px["close"].values, index=idx)
    fwd = close.shift(-(horizon_days - 1)) / open_ - 1
    df = pd.DataFrame({"fng": fng, "fwd": fwd * 100}).dropna()
    return df[(df.index >= START) & (df.index <= END)]


def excess_series(df, bucket):
    """Per day: the forward return minus the all-days mean where the bucket holds, else NaN.
    Its mean is the bucket's excess; keeping the calendar order lets the blocks resample days."""
    sel = df["fng"].map(BUCKETS[bucket])
    return (df["fwd"] - df["fwd"].mean()).where(sel)


def score(df):
    out = {}
    for h, (bucket, sign) in PRIMARY.items():
        ex = excess_series(df, bucket)
        days = int(ex.notna().sum())
        mean = float(ex.mean()) if days else float("nan")
        # Resample the calendar, then average the bucket's days inside each draw.
        x, m = ex.fillna(0.0).to_numpy(), ex.notna().to_numpy(float)
        rng = np.random.default_rng(SEED)
        n, k = len(x), -(-len(x) // BLOCK)
        starts = rng.integers(0, max(n - BLOCK, 1), size=(N_BOOT, k))
        idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(N_BOOT, -1)[:, :n]
        with np.errstate(invalid="ignore"):
            boot = x[idx].sum(1) / m[idx].sum(1)
        boot = boot[np.isfinite(boot)]
        lo, hi = float(np.percentile(boot, 5)), float(np.percentile(boot, 95))
        halves = [float(excess_series(part, bucket).mean()) for part in (df[df.index < HALF], df[df.index >= HALF])]
        if days < MIN_DAYS:
            v = "INCONCLUSIVE"
        elif sign * mean <= 0:
            v = "FAIL"
        elif (lo > 0 if sign > 0 else hi < 0) and all(sign * hh > 0 for hh in halves):
            v = "PASS"
        else:
            v = "INCONCLUSIVE"
        out[h] = {"bucket": bucket, "days": days, "excess_pp": mean, "ci90": [lo, hi], "halves": halves, "verdict": v}
    out["descriptive"] = {b: {"days": int(df["fng"].map(f).sum()),
                              "excess_pp": float(excess_series(df, b).mean())} for b, f in BUCKETS.items()}
    out["descriptive"]["spearman_ic"] = float(df["fng"].rank().corr(df["fwd"].rank()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true", help="day counts per bucket only, no returns")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    fng = load_fng()
    if args.smoke:
        w = fng[(fng.index >= START) & (fng.index <= END)]
        print({b: int(w.map(f).sum()) for b, f in BUCKETS.items()}, "days", len(w))
        return 0
    df = frame(fng, load_btc_daily())
    res = {"days": len(df), "window": [str(df.index.min().date()), str(df.index.max().date())], **score(df)}
    for hz in (1, 7):
        d2 = frame(fng, load_btc_daily(), horizon_days=hz)
        res["descriptive"][f"spearman_ic_{hz}d"] = float(d2["fng"].rank().corr(d2["fwd"].rank()))
    print(json.dumps(res, indent=1, default=str))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
