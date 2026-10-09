#!/usr/bin/env python3
"""Score run 3's H-B: does the engine beat random entry on BTC?
`docs/superpowers/specs/2026-10-09-run3-prereg.md` § H-B.

BTC/USDT 4h from 2026-08-30 to run 3's end. No experiment here has read these candles
(Nakhoda's cache stops at 2026-08-30). They are fetched from Binance's public spot mirror,
with history from 2018 so the 1W EMA200 is real (`entry_ic --htf-warmup strict`). The
`spotsignal` arm (engine as deployed) runs against a count-matched random arm (20 seeds)
through the same exits and costs, at the fixed SPOT_THRESHOLD, as in `entry_ic.py`'s
independent-entry method, unchanged.

    ./venv/bin/python scripts/hb_eval.py --end 2026-11-08 --out-dir docs/superpowers/specs/<run>/hb

PASS  spotsignal.mean − random.mean > 0 AND spotsignal's 90% CI excludes random.mean
FAIL  spotsignal.mean ≤ random.mean
n(spotsignal) < 30 → INCONCLUSIVE (the verdict waits, up to run 3's end).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES  # noqa: E402
from scripts.entry_ic import (build_htf, entries_engine, entries_random, eval_start,  # noqa: E402
                              load_symbol, run_entries, summarise)

WINDOW_START = "2026-08-30"
HISTORY_START = datetime(2018, 1, 1, tzinfo=UTC)
MIN_N = 30
SEEDS = 20
MIRROR = "https://data-api.binance.vision/api/v3/klines"
BAR_MS = 4 * 3_600_000


def fetch(end: pd.Timestamp, out: Path) -> Path:
    """BTCUSDT 4h CLOSED bars opening before `end`, in entry_ic's cache format."""
    rows, start, end_ms = [], int(HISTORY_START.timestamp() * 1000), int(end.timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    with requests.Session() as s:
        while start < end_ms:
            r = s.get(MIRROR, params={"symbol": "BTCUSDT", "interval": "4h", "startTime": start,
                                      "endTime": end_ms - 1, "limit": 1000}, timeout=30)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows += [b for b in batch if int(b[6]) < now_ms]          # closed bars only
            start = int(batch[-1][0]) + BAR_MS
            time.sleep(0.2)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "BTC_USDT_4h.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        w.writerows([b[0], b[1], b[2], b[3], b[4], b[5]] for b in rows)
    return path


def verdict(spot: dict, rnd_mean: float) -> str:
    if spot["n"] < MIN_N:
        return "INCONCLUSIVE"
    if spot["mean"] <= rnd_mean:
        return "FAIL"
    lo, hi = spot["ci"]
    return "PASS" if not (lo <= rnd_mean <= hi) else "INCONCLUSIVE"


def score(df: pd.DataFrame, start_date: str = WINDOW_START, seeds: int = SEEDS) -> dict:
    frames = build_htf(df)
    start = eval_start(df, frames, start_date, "strict")
    first_eval = df.index[start]
    assert first_eval >= pd.Timestamp(start_date), f"evaluated {first_eval} < {start_date} — discard"
    max_hold = MAX_HOLD_CANDLES["4h"]
    eng = entries_engine(df, frames, start)                  # gates_disabled=None → as deployed
    spot_p, spot_open = run_entries(df, eng, max_hold)
    rnd = []
    for sd in range(seeds):
        p, _ = run_entries(df, entries_random(df, len(eng), seed=sd, lo=start), max_hold)
        rnd += p
    s, r = summarise(spot_p), summarise(rnd)
    return {"span": [str(first_eval), str(df.index[-1])], "engine_buys": len(eng),
            "spotsignal": s, "spotsignal_unresolved": spot_open, "random": r,
            "diff": s["mean"] - r["mean"] if s["n"] and r["n"] else None,
            "H-B": verdict(s, r["mean"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", required=True, help="run 3's end (exclusive), YYYY-MM-DD[THH:MM]")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--csv", type=Path, default=None, help="reuse a fetched CSV instead of fetching")
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    end = pd.Timestamp(args.end, tz="UTC")
    path = args.csv or fetch(end, args.out_dir)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    df = load_symbol("BTC", path.parent, end=str(end.tz_localize(None)))
    res = {"csv": str(path), "sha256": sha, **score(df)}
    s, r = res["spotsignal"], res["random"]
    print(f"H-B  BTC 4h {res['span'][0]} → {res['span'][1]}  sha256 {sha[:12]}…")
    print(f"  spotsignal n={s['n']} mean={s['mean']:+.3f}pp CI=[{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}] "
          f"(unresolved {res['spotsignal_unresolved']})")
    print(f"  random     n={r['n']} mean={r['mean']:+.3f}pp ({SEEDS} seeds)")
    print(f"  → H-B {res['H-B']}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
