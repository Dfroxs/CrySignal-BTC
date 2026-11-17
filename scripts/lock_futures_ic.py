#!/usr/bin/env python3
"""Score H-PLK exactly as pre-registered: `docs/superpowers/specs/2026-11-11-futures-profit-lock-prereg.md`.

Does a breakeven lock help futures' exit? One candidate: once a bar's CLOSE is favourable by
1.0 x the position's own stop distance, the trail is raised to entry from the next bar on,
through `_simulate_forward`'s `exit_params["be_lock_r"]`. Everything else as live. Every
entry runs with and without the lock and the comparison is paired.

  A  synthetic BUY entries (exit_ic method), BTC ETH BNB XRP LINK, 2024-2025, 10 cells
  B  synthetic BUY entries, same assets, 2020-2023, 20 cells
  C  engine entries, every signal run_backtest simulates for BTC futures (guard):
     primary 2022-01-01 -> 2025-10-01 (contaminated), confirmation 2025-10-01 -> 2026-10-09

Arms, windows, criteria and discard checks are H-TPF's and H-TRL's. Candidate-independent
helpers are imported, not copied, so the three instruments cannot drift. Only what depends
on the candidate lives here.

    ./venv/bin/python scripts/lock_futures_ic.py --smoke     # counts only, no P&L
    ./venv/bin/python scripts/lock_futures_ic.py --out docs/superpowers/specs/2026-11-11-futures-profit-lock-run/result.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import RESOLVED, _exit_signal, _simulate_forward  # noqa: E402
from scripts.exit_ic import _signal_at, synth_entries  # noqa: E402
from scripts.tp1_futures_ic import (  # noqa: E402
    ARM_YEARS, ASSETS, FID_TOL, HOLD, MIN_CELLS, MIN_EFFECT, MODE, STRIDE, TF,
    WINDOWS, fetch_cell, geometry_ok, pool_c, verdict,
)
from scripts.trail_futures_ic import (  # noqa: E402
    excursion_giveback, record_window, summarise_rows as _summarise,
)

LOCK_R = 1.0
CAND_PARAMS = {"be_lock_r": LOCK_R}
MIN_ARMED = 0.10                       # prereg, Discard: lock inert below this share on Arm C


def _run(df, i, sig, params=None):
    """(pnl, resolved, time_exit, giveback, lock_armed, exited_at_floor) on a fresh copy."""
    s = dict(sig)
    t = _simulate_forward(df, i, s, HOLD, TF, MODE, exit_params=params)
    if t is None:
        return 0.0, False, False, 0.0, False, False
    armed = bool(s.get("_lock_armed"))
    at_floor = (armed and not s.get("_partial_taken")
                and abs(float(t["exit_price"]) - round(float(sig["entry_price"]), 2)) < 0.005)
    return (float(t["pnl_pct"]), t["outcome"] in RESOLVED, t["outcome"] == "TIME_EXIT",
            excursion_giveback(df, i, t, sig), armed, at_floor)


def make_row(df, i, sig):
    """(base, cand, base_res, cand_res, te_base, te_cand, gb_base, gb_cand, armed, at_floor)."""
    pb, rb, tb, gb, _, _ = _run(df, i, sig)
    pc, rc, tc, gc, armed, floor = _run(df, i, sig, CAND_PARAMS)
    return (pb, pc, rb, rc, tb, tc, gb, gc, armed, floor)


def summarise_rows(rows):
    n = len(rows)
    s = _summarise(rows)
    s["lock_armed"] = sum(r[8] for r in rows) / n if n else 0.0
    s["exit_at_floor"] = sum(r[9] for r in rows) / n if n else 0.0
    return s


def run_cell(symbol, year):
    df = fetch_cell(symbol, year)
    entries = synth_entries(df, STRIDE, tail=HOLD + 2)
    if not entries:
        raise ValueError(f"no synthetic entries for {symbol} {year}: len(df)={len(df)}")
    rows = []
    for i in entries:
        raw = _signal_at(df, i)
        if not (raw["atr"] > 0):
            rows.append((0.0, 0.0, False, False, False, False, 0.0, 0.0, False, False))
            continue
        rows.append(make_row(df, i, _exit_signal(raw, MODE)))
    return rows


def arm_synthetic(arm):
    cells, pooled, btc_rows, other_rows = [], [], [], []
    for sym in ASSETS:
        for year in ARM_YEARS[arm]:
            rows = run_cell(sym, year)
            s = summarise_rows(rows)
            s.update(symbol=sym, year=year)
            cells.append(s)
            pooled += rows
            (btc_rows if sym == "BTC/USDT" else other_rows).extend(rows)
            print(f"  {arm} {sym:10s} {year}  n={s['n']:4d} n_eff={s['n_eff']:4d} "
                  f"diff={s['mean_diff']:+.4f}pp  base={s['base_mean']:+.3f} cand={s['cand_mean']:+.3f}  "
                  f"armed {s['lock_armed']:.0%} floor-exit {s['exit_at_floor']:.0%}", flush=True)
    return {"cells": cells, "pooled": summarise_rows(pooled), "btc": summarise_rows(btc_rows),
            "ex_btc": summarise_rows(other_rows),
            "cells_favouring": sum(1 for c in cells if c["mean_diff"] > 0),
            "cells_total": len(cells)}


def arm_c_window(name):
    df, calls = record_window(*WINDOWS[name])
    if df is None or not calls:
        raise RuntimeError(f"Arm C {name}: no signals recorded")
    rows, by_dir, armed, max_diff, compared = [], {"BUY": [], "SELL": []}, 0, 0.0, 0
    for i, sig, own in calls:
        row = make_row(df, i, sig)
        if own is not None:
            max_diff = max(max_diff, abs(row[0] - float(own["pnl_pct"])))
            compared += 1
        armed += row[8]
        if row[2] and row[3]:
            rows.append(row)
            by_dir[sig["type"]].append(row[1] - row[0])
    s = summarise_rows(rows)
    s.update(signals_recorded=len(calls), armed_share=armed / len(calls),
             fidelity={"compared": compared, "max_abs_diff": max_diff,
                       "ok": compared == len(calls) and max_diff <= FID_TOL},
             by_dir={k: {"n": len(v), "mean_diff": sum(v) / len(v) if v else 0.0}
                     for k, v in by_dir.items()})
    return s, by_dir


def arm_meets(arm, r):
    return r["cells_favouring"] >= MIN_CELLS[arm] and r["pooled"]["mean_diff"] >= MIN_EFFECT


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
        print(f"smoke A BTC/USDT 2024: synthetic entries "
              f"{len(synth_entries(df, STRIDE, tail=HOLD + 2))}")
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
              f"armed={s['armed_share']:.0%}  fidelity max|d|={s['fidelity']['max_abs_diff']:.2e}",
              flush=True)
    c = pool_c(per_window, dirs)

    discards = []
    if not all(w["fidelity"]["ok"] for w in per_window.values()):
        discards.append("Arm C baseline replay != run_backtest's own P&L")
    inert_arms = [w for w, s in per_window.items() if s["armed_share"] < MIN_ARMED]

    v = "DISCARDED" if discards else verdict(a, b, c)
    for arm, r in (("A", a), ("B", b)):
        p = r["pooled"]
        print(f"\n{arm}: cells favouring {r['cells_favouring']}/{r['cells_total']} "
              f"(need {MIN_CELLS[arm]}), pooled {p['mean_diff']:+.4f}pp (need >= {MIN_EFFECT:+.2f}), "
              f"BTC {r['btc']['mean_diff']:+.4f}pp, ex-BTC {r['ex_btc']['mean_diff']:+.4f}pp, "
              f"armed {p['lock_armed']:.0%}, exit at floor {p['exit_at_floor']:.0%}, "
              f"TIME_EXIT {p['time_exit_base']:.0%}->{p['time_exit_cand']:.0%}, "
              f"give-back {p['giveback_base']:.2f}->{p['giveback_cand']:.2f}pp")
    print("C by direction (pooled): " + "  ".join(
        f"{k} n={x['n']} {x['mean_diff']:+.4f}pp" for k, x in c["by_dir"].items()))
    if inert_arms:
        print(f"note: lock armed on < {MIN_ARMED:.0%} of Arm C entries in {inert_arms}")
    for d in discards:
        print(f"DISCARD: {d}")
    print(f"\nVERDICT: {v}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"verdict": v, "A": a, "B": b, "C": c, "discards": discards,
             "lock_armed_under_10pct": inert_arms,
             "config": {"lock_r": LOCK_R, "stride": STRIDE, "min_effect": MIN_EFFECT,
                        "windows": WINDOWS, "hold": HOLD}}, indent=1))
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
