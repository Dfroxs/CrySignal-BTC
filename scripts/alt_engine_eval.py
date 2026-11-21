#!/usr/bin/env python3
"""H-EA: does the BTC bot's rule set pick direction on the scanner's altcoins?

Pre-registration: `docs/superpowers/specs/2026-11-21-alt-engine-prereg.md`. Offline, run once,
on a workstation (it holds a year of 1h bars per cell; the VPS is never touched).

Every trade `backtest.run_backtest` takes is paired with its mirror twin: the same entry bar
and price in the opposite direction, every level reflected about the entry, through the same
exit simulator. `d = engine − mirror` per closed trade isolates direction skill.

    ./venv/bin/python scripts/alt_engine_eval.py [--out result.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COINS = ("ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX")     # prereg: fixed
CELLS = [(c, y) for c in COINS for y in (2024, 2025)]
MIN_TRADES, MIN_POSITIVE = 10, 12
N_BOOT, SEED = 5000, 2026


def mirror(signal):
    """The same entry in the opposite direction, every level reflected about the entry."""
    e = signal["entry_price"]
    m = dict(signal)
    m["type"] = "SELL" if signal["type"] == "BUY" else "BUY"
    for k in ("stop_loss", "take_profit", "tp2"):
        if signal.get(k):
            m[k] = 2 * e - signal[k]
    return m


def verdict(cells):
    """`cells` = {name: [d per closed trade]}. Criteria per the prereg."""
    means = {k: (float(np.mean(v)) if len(v) >= MIN_TRADES else None) for k, v in cells.items()}
    positive = sum(1 for m in means.values() if m is not None and m > 0)
    pooled = [x for v in cells.values() for x in v]
    mean = float(np.mean(pooled)) if pooled else float("nan")
    keys = list(cells)
    rng = np.random.default_rng(SEED)
    boot = []
    for _ in range(N_BOOT):
        pick = rng.integers(0, len(keys), len(keys))
        xs = [x for i in pick for x in cells[keys[i]]]
        boot.append(np.mean(xs) if xs else np.nan)
    lo, hi = (float(np.nanpercentile(boot, 5)), float(np.nanpercentile(boot, 95))) if boot else (np.nan, np.nan)
    ok = positive >= MIN_POSITIVE and lo > 0
    return {"verdict": "PASS" if ok else "FAIL", "positive_cells": positive, "cells": len(cells),
            "pooled_mean_d": mean, "ci90": [lo, hi], "cell_means": means}


def run_cell(coin, year):
    """(trades, d list, mirror outcomes) for one cell, via the real backtest."""
    import backtest
    pairs = []                                            # (entry_time, engine result, mirror result)
    original = backtest._simulate_forward

    def twin(df, entry_idx, signal, *a, **k):
        res = original(df, entry_idx, signal, *a, **k)
        if res:
            pairs.append((str(df.index[entry_idx]), res, original(df, entry_idx, mirror(signal), *a, **k)))
        return res

    backtest._simulate_forward = twin
    try:
        trades, _ = backtest.run_backtest(f"{coin}/USDT", "1h", "futures",
                                          start=f"{year}-01-01", end=f"{year}-12-31")
    finally:
        backtest._simulate_forward = original
    taken = {t["entry_time"] for t in trades}
    d, eng, out = [], [], {}
    for when, a, b in pairs:
        if when not in taken or not b or a["outcome"] == "OPEN" or b["outcome"] == "OPEN":
            continue
        d.append(a["pnl_pct"] - b["pnl_pct"])
        eng.append(a["pnl_pct"])
        out[a["outcome"]] = out.get(a["outcome"], 0) + 1
    return d, eng, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    cells, report = {}, {}
    for coin, year in CELLS:
        d, eng, out = run_cell(coin, year)
        key = f"{coin} {year}"
        cells[key] = d
        report[key] = {"trades": len(d), "mean_d": float(np.mean(d)) if d else None,
                       "engine_net_per_trade": float(np.mean(eng)) if eng else None,
                       "engine_win_rate": float(np.mean([x > 0 for x in eng])) if eng else None,
                       "outcomes": out}
        print(f"{key}: {len(d)} trades, mean d {report[key]['mean_d']}, engine/trade "
              f"{report[key]['engine_net_per_trade']}", flush=True)
    v = verdict(cells)
    print(f"\nH-EA-1: {v['verdict']}  positive cells {v['positive_cells']}/{v['cells']} (need {MIN_POSITIVE})  "
          f"pooled mean d {v['pooled_mean_d']:+.3f}pp  CI90 [{v['ci90'][0]:+.3f}, {v['ci90'][1]:+.3f}]")
    if args.out:
        Path(args.out).write_text(json.dumps({"verdict": v, "cells": report}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
