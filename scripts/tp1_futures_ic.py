#!/usr/bin/env python3
"""Score H-TPF exactly as pre-registered: `docs/superpowers/specs/2026-11-10-futures-tp1-prereg.md`.

Is futures' TP1 too far away? One candidate: TP1 at 1.0 x the position's own stop distance,
TP2 at twice that, applied at open AFTER `apply_futures_exit_geometry`, so entries and gates
stay as live. Every entry runs under both levels and the comparison is paired.

  A  synthetic BUY entries (exit_ic method), BTC ETH BNB XRP LINK, 2024-2025, 10 cells
  B  synthetic BUY entries, same assets, 2020-2023, 20 cells
  C  engine entries, every signal run_backtest simulates for BTC futures (guard):
     primary 2022-01-01 -> 2025-10-01 (contaminated), confirmation 2025-10-01 -> 2026-10-09

    ./venv/bin/python scripts/tp1_futures_ic.py --smoke     # counts only, no P&L
    ./venv/bin/python scripts/tp1_futures_ic.py --out docs/superpowers/specs/2026-11-10-futures-tp1-run/result.json
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest  # noqa: E402
from backtest import MAX_HOLD_CANDLES, RESOLVED, _exit_signal, _simulate_forward  # noqa: E402
from config import FUTURES_CONFIG  # noqa: E402
from scripts.exit_ic import _signal_at, synth_entries  # noqa: E402
from signals.ohlcv import fetch_ohlcv_df  # noqa: E402

TF, MODE = "1h", "futures"
HOLD = MAX_HOLD_CANDLES[TF]
ASSETS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "LINK/USDT"]
ARM_YEARS = {"A": [2024, 2025], "B": [2020, 2021, 2022, 2023]}
MIN_CELLS = {"A": 8, "B": 16}
STRIDE = 24                            # one synthetic entry a day (prereg, Arms)
MIN_EFFECT = 0.05                      # pp per entry
CAND_RR = 1.0                          # TP1 at 1.0 x the stop distance
INERT_MAX = 0.90                       # prereg, Discard: candidate inert above this share
FID_TOL = 1e-9
WINDOWS = {"primary": ("2022-01-01", "2025-10-01"),
           "confirmation": ("2025-10-01", "2026-10-09")}
RETRIES = 5


def candidate(sig):
    """The candidate's levels for `sig` (already carrying live levels), either direction.

    `TP1' = entry +/- min(|take_profit - entry|, CAND_RR * |entry - stop_loss|)` and
    `TP2' = entry +/- min(|tp2 - entry|, 2 * |TP1' - entry|)`. A target already nearer is
    never moved further out.
    """
    entry = sig["entry_price"]
    sign = 1.0 if sig["type"] == "BUY" else -1.0
    d1 = min(abs(sig["take_profit"] - entry), CAND_RR * abs(entry - sig["stop_loss"]))
    far2 = abs(sig["tp2"] - entry) if sig.get("tp2") else float("inf")
    d2 = min(far2, 2 * d1)
    return dict(sig, take_profit=entry + sign * d1, tp2=entry + sign * d2)


def geometry_ok():
    """Discard check: the baseline's stop is `stop_distance_mult` x the engine's own stop,
    i.e. the geometry is applied exactly once. False discards the run."""
    mult = FUTURES_CONFIG.get("stop_distance_mult", 1.0)
    raw = {"type": "BUY", "entry_price": 100.0, "stop_loss": 98.0,
           "take_profit": 105.0, "tp2": 110.0, "atr": 1.0}
    live = _exit_signal(copy.deepcopy(raw), MODE)
    return abs((live["entry_price"] - live["stop_loss"]) - mult * 2.0) < 1e-9


def _run(df, i, sig):
    """(pnl, resolved, tp1_hit) for one entry under `sig`'s levels, on a fresh copy."""
    s = dict(sig)
    t = _simulate_forward(df, i, s, HOLD, TF, MODE)
    if t is None:
        return 0.0, False, False
    return float(t["pnl_pct"]), t["outcome"] in RESOLVED, bool(s.get("_partial_taken"))


def fetch_cell(symbol, year):
    since = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
    for attempt in range(RETRIES):
        try:
            df = fetch_ohlcv_df(symbol, TF, since=since, until=until)
            if df is not None and len(df):
                return df
        except Exception as exc:  # a cell is retried until it yields a row, never dropped
            logging.warning("fetch %s %s attempt %d: %s", symbol, year, attempt + 1, exc)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"no data for {symbol} {year} after {RETRIES} tries")


def run_cell(symbol, year):
    """Paired rows (base, cand, base_resolved, cand_resolved, hit_base, hit_cand)."""
    df = fetch_cell(symbol, year)
    entries = synth_entries(df, STRIDE, tail=HOLD + 2)
    if not entries:
        raise ValueError(f"no synthetic entries for {symbol} {year}: len(df)={len(df)}")
    rows = []
    for i in entries:
        raw = _signal_at(df, i)
        if not (raw["atr"] > 0):
            rows.append((0.0, 0.0, False, False, False, False))
            continue
        base = _exit_signal(raw, MODE)
        pb, rb, hb = _run(df, i, base)
        pc, rc, hc = _run(df, i, candidate(base))
        rows.append((pb, pc, rb, rc, hb, hc))
    return rows


def summarise_rows(rows):
    n = len(rows)
    diffs = [r[1] - r[0] for r in rows]
    return {"n": n, "n_eff": sum(1 for d in diffs if d != 0),
            "mean_diff": sum(diffs) / n if n else 0.0,
            "base_mean": sum(r[0] for r in rows) / n if n else 0.0,
            "cand_mean": sum(r[1] for r in rows) / n if n else 0.0,
            "unresolved_base": sum(1 for r in rows if not r[2]),
            "unresolved_cand": sum(1 for r in rows if not r[3]),
            "tp1_hit_base": sum(r[4] for r in rows) / n if n else 0.0,
            "tp1_hit_cand": sum(r[5] for r in rows) / n if n else 0.0}


def arm_synthetic(arm):
    cells, pooled, btc_rows = [], [], []
    for sym in ASSETS:
        for year in ARM_YEARS[arm]:
            rows = run_cell(sym, year)
            s = summarise_rows(rows)
            s.update(symbol=sym, year=year)
            cells.append(s)
            pooled += rows
            if sym == "BTC/USDT":
                btc_rows += rows
            print(f"  {arm} {sym:10s} {year}  n={s['n']:4d} n_eff={s['n_eff']:4d} "
                  f"diff={s['mean_diff']:+.4f}pp  base={s['base_mean']:+.3f} cand={s['cand_mean']:+.3f}  "
                  f"TP1 hit {s['tp1_hit_base']:.0%}->{s['tp1_hit_cand']:.0%}", flush=True)
    return {"cells": cells, "pooled": summarise_rows(pooled), "btc": summarise_rows(btc_rows),
            "cells_favouring": sum(1 for c in cells if c["mean_diff"] > 0),
            "cells_total": len(cells)}


def record_window(start, end):
    """(df, [(entry_idx, signal, run_backtest's own trade)]) for every signal simulated.

    Recorded by wrapping `backtest._simulate_forward`, as trail_ic does, and keeping the
    trade run_backtest itself got so the baseline replay can be checked against it.
    """
    calls, frame = [], {}
    original = backtest._simulate_forward

    def spy(df, entry_idx, signal, *a, **kw):
        frame["df"] = df
        sig = copy.deepcopy(signal)
        trade = original(df, entry_idx, signal, *a, **kw)
        calls.append((entry_idx, sig, trade))
        return trade

    backtest._simulate_forward = spy
    try:
        backtest.run_backtest(mode=MODE, timeframe=TF, start=start, end=end,
                              counterfactual=True)
    finally:
        backtest._simulate_forward = original
    return frame.get("df"), calls


def arm_c_window(name):
    """Paired signals resolved in both arms, plus the fidelity and inert figures."""
    df, calls = record_window(*WINDOWS[name])
    if df is None or not calls:
        raise RuntimeError(f"Arm C {name}: no signals recorded")
    rows, by_dir, inert, max_diff, compared = [], {"BUY": [], "SELL": []}, 0, 0.0, 0
    for i, sig, own in calls:
        pb, rb, hb = _run(df, i, sig)
        if own is not None:
            max_diff = max(max_diff, abs(pb - float(own["pnl_pct"])))
            compared += 1
        cand = candidate(sig)
        pc, rc, hc = _run(df, i, cand)
        inert += abs(cand["take_profit"] - sig["take_profit"]) < 1e-9
        if rb and rc:
            rows.append((pb, pc, rb, rc, hb, hc))
            by_dir[sig["type"]].append(pc - pb)
    s = summarise_rows(rows)
    s.update(signals_recorded=len(calls), inert_share=inert / len(calls),
             fidelity={"compared": compared, "max_abs_diff": max_diff,
                       "ok": compared == len(calls) and max_diff <= FID_TOL},
             by_dir={k: {"n": len(v), "mean_diff": sum(v) / len(v) if v else 0.0}
                     for k, v in by_dir.items()})
    return s, by_dir


def arm_meets(arm, arm_result):
    return (arm_result["cells_favouring"] >= MIN_CELLS[arm]
            and arm_result["pooled"]["mean_diff"] >= MIN_EFFECT)


def verdict(a, b, c):
    """Prereg, Verdict. `c` maps window -> summary; its by_dir is pooled over both windows."""
    if a["pooled"]["mean_diff"] <= 0 or b["pooled"]["mean_diff"] <= 0:
        return "FAIL"
    if not (arm_meets("A", a) and arm_meets("B", b)):
        return "INCONCLUSIVE"
    c_holds = all(w["mean_diff"] > 0 for w in c["windows"].values()) \
        and all(v["mean_diff"] > 0 for v in c["by_dir"].values())
    return "PASS" if c_holds else "INCONCLUSIVE"


def pool_c(per_window, dirs):
    out = {k: {"n": len(v), "mean_diff": sum(v) / len(v) if v else 0.0}
           for k, v in dirs.items()}
    return {"windows": per_window, "by_dir": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--smoke", action="store_true", help="counts only; no P&L is printed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("backtest").setLevel(logging.ERROR)

    if not geometry_ok():
        print("DISCARDED: baseline stop distance != stop_distance_mult x engine stop")
        return 2
    print("geometry applied once: ok")

    if args.smoke:
        df = fetch_cell("BTC/USDT", 2024)
        n = len(synth_entries(df, STRIDE, tail=HOLD + 2))
        print(f"smoke A BTC/USDT 2024: synthetic entries {n}")
        for w in WINDOWS:
            _, calls = record_window(*WINDOWS[w])
            print(f"smoke C {w}: signals recorded {len(calls)}")
        return 0

    print("Arm A (2024-2025, synthetic)")
    a = arm_synthetic("A")
    print("Arm B (2020-2023, synthetic)")
    b = arm_synthetic("B")
    per_window, dirs = {}, {"BUY": [], "SELL": []}
    for w in WINDOWS:
        s, by_dir = arm_c_window(w)
        per_window[w] = s
        for k in dirs:
            dirs[k] += by_dir[k]
        print(f"  C {w:12s} recorded={s['signals_recorded']:4d} paired={s['n']:4d} "
              f"diff={s['mean_diff']:+.4f}pp  base={s['base_mean']:+.3f} cand={s['cand_mean']:+.3f}  "
              f"TP1 hit {s['tp1_hit_base']:.0%}->{s['tp1_hit_cand']:.0%}  "
              f"inert={s['inert_share']:.0%}  fidelity max|d|={s['fidelity']['max_abs_diff']:.2e}",
              flush=True)
    c = pool_c(per_window, dirs)

    discards = []
    if not all(w["fidelity"]["ok"] for w in per_window.values()):
        discards.append("Arm C baseline replay != run_backtest's own P&L")
    inert_arms = [w for w, s in per_window.items() if s["inert_share"] > INERT_MAX]

    v = "DISCARDED" if discards else verdict(a, b, c)
    for arm, r in (("A", a), ("B", b)):
        print(f"\n{arm}: cells favouring {r['cells_favouring']}/{r['cells_total']} "
              f"(need {MIN_CELLS[arm]}), pooled {r['pooled']['mean_diff']:+.4f}pp "
              f"(need >= {MIN_EFFECT:+.2f}), TP1 hit {r['pooled']['tp1_hit_base']:.0%}->"
              f"{r['pooled']['tp1_hit_cand']:.0%}, BTC {r['btc']['mean_diff']:+.4f}pp")
    print("C by direction (pooled): " + "  ".join(
        f"{k} n={x['n']} {x['mean_diff']:+.4f}pp" for k, x in c["by_dir"].items()))
    if inert_arms:
        print(f"note: candidate inert on > {INERT_MAX:.0%} of Arm C entries in {inert_arms}")
    for d in discards:
        print(f"DISCARD: {d}")
    print(f"\nVERDICT: {v}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"verdict": v, "A": a, "B": b, "C": c, "discards": discards,
             "inert_over_90pct": inert_arms,
             "config": {"cand_rr": CAND_RR, "stride": STRIDE, "min_effect": MIN_EFFECT,
                        "windows": WINDOWS, "hold": HOLD}}, indent=1))
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
