#!/usr/bin/env python3
"""Score H-TP exactly as pre-registered: `docs/superpowers/specs/2026-10-15-tp1-distance-prereg.md`.

Is spot's TP1 (+3.75 ATR, 2.5 x the 1.5-ATR risk unit) too far away? One candidate:
TP1 at 1.5 x risk (+2.25 ATR), TP2 at twice that, applied to a position's levels at open
so entries and the engine's R:R veto stay as live. Every entry runs under both levels
and the comparison is paired.

  A  synthetic entries (exit_ic method), BTC ETH BNB XRP LINK, 2024-2025, 10 cells
  B  synthetic entries, the 09-21 grid, same assets, 2020-2023, 20 cells
  C  engine entries, H-SR's kept universe on Nakhoda's OKX cache (guard; contaminated)

    ./venv/bin/python scripts/tp1_ic.py --out docs/superpowers/specs/2026-10-15-tp1-distance-run/result.json
    ./venv/bin/python scripts/tp1_ic.py --smoke     # counts only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import RESOLVED, _score_candle, _simulate_forward  # noqa: E402
from config import RISK_CONFIG, SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import TUNING, build_htf, eval_start, load_symbol  # noqa: E402
from scripts.exit_ic import _signal_at, synth_entries  # noqa: E402
from scripts.spot_gates_ic import END, MAX_HOLD, _window, gates_for  # noqa: E402

ASSETS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "LINK/USDT"]
ARM_YEARS = {"A": [2024, 2025], "B": [2020, 2021, 2022, 2023]}
MIN_CELLS = {"A": 8, "B": 16}
STRIDE = 6
MIN_EFFECT = 0.05                      # pp per entry
CAND_RR = 1.5                          # TP1 at 1.5 x risk
RISK_ATR = RISK_CONFIG["atr_multiplier"]
FIDELITY = {"primary": (347, -0.165), "confirmation": (43, -0.515)}
RETRIES = 5


def candidate(sig):
    """The candidate's levels for `sig`: never further out than the engine's own."""
    entry, atr = sig["entry_price"], float(sig["atr"])
    tp1 = min(sig["take_profit"], entry + CAND_RR * RISK_ATR * atr)
    tp2 = min(sig.get("tp2") or float("inf"), entry + 2 * (tp1 - entry))
    return dict(sig, take_profit=tp1, tp2=tp2)


def _run(df, i, sig, tf="4h"):
    """(pnl, resolved, tp1_hit) for one entry under `sig`'s levels, on a fresh copy."""
    s = dict(sig)
    t = _simulate_forward(df, i, s, MAX_HOLD, tf, "spot")
    if t is None:
        return 0.0, False, False
    return float(t["pnl_pct"]), t["outcome"] in RESOLVED, bool(s.get("_partial_taken"))


def run_cell(symbol, year):
    from signals.ohlcv import fetch_ohlcv_df
    since = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
    for attempt in range(RETRIES):
        try:
            df = fetch_ohlcv_df(symbol, "4h", since=since, until=until)
            break
        except Exception as exc:  # noqa: BLE001 — prereg: retry until it produces a row
            logging.warning("fetch %s %s failed (%s), retry %d", symbol, year, exc, attempt + 1)
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"{symbol} {year}: no data after {RETRIES} attempts")
    entries = synth_entries(df, STRIDE, tail=MAX_HOLD + 2)
    if not entries:
        raise ValueError(f"no synthetic entries for {symbol} {year}")
    rows = []
    for i in entries:
        base = _signal_at(df, i)
        if not base["atr"] > 0:
            rows.append((0.0, 0.0, False, False, False, False))
            continue
        pb, rb, hb = _run(df, i, base)
        pc, rc, hc = _run(df, i, candidate(base))
        rows.append((pb, pc, rb, rc, hb, hc))
    return rows


def summarise_rows(rows):
    n = len(rows)
    diffs = [c - b for b, c, *_ in rows]
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
                  f"TP1 hit {s['tp1_hit_base']:.0%}→{s['tp1_hit_cand']:.0%}", flush=True)
    p = summarise_rows(pooled)
    favour = sum(1 for c in cells if c["mean_diff"] > 0)
    ok = favour >= MIN_CELLS[arm] and p["mean_diff"] >= MIN_EFFECT
    return {"cells": cells, "cells_favouring": favour, "cells_total": len(cells),
            "pooled": p, "btc": summarise_rows(btc_rows),
            "meets_criteria": ok, "pooled_le_zero": p["mean_diff"] <= 0}


def run_symbol_c(args):
    """Arm C for one symbol: H-SR's kept universe, de-duplicated on baseline exits."""
    sym, smoke = args
    logging.disable(logging.WARNING)
    df = load_symbol(sym, end=str(END.date()))
    frames = build_htf(df)
    start = eval_start(df, frames, None, "strict")
    stop = min(len(df) - 1, start + 400) if smoke else len(df) - 1
    kept = []
    for i in range(start, stop):
        window, sig = _score_candle(df, i, "4h", "spot", frames, SPOT_THRESHOLD, None)
        if sig["type"] != "BUY" or sig.get("confidence") not in ("NORMAL", "STRONG"):
            continue
        gates, _ = gates_for(sym, sig, window)
        if not gates:
            kept.append((i, sig))
    if smoke:
        return {"symbol": sym, "kept_bars": len(kept)}
    out = {"symbol": sym, "windows": {}}
    for w in ("primary", "confirmation"):
        trades, open_until = [], -1
        for i, sig in sorted((k for k in kept if _window(df.index[k[0]]) == w), key=lambda k: k[0]):
            if i <= open_until:
                continue
            s = dict(sig)
            t = _simulate_forward(df, i, s, MAX_HOLD, "4h", "spot")
            if t is None:
                continue
            open_until = i + t["candles_held"]
            if t["outcome"] not in RESOLVED:
                continue
            cand = candidate(sig)
            pc, rc, hc = _run(df, i, cand)
            trades.append((float(t["pnl_pct"]), pc, True, rc, bool(s.get("_partial_taken")), hc,
                           abs(cand["take_profit"] - sig["take_profit"]) < 1e-9))
        out["windows"][w] = trades
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    if args.smoke:
        rows = run_cell("BTC/USDT", 2024)
        print(f"smoke A BTC/USDT 2024: synthetic entries {len(rows)}")
        c = run_symbol_c(("BTC", True))
        print(f"smoke C BTC first 400 candles: kept bars {c['kept_bars']}")
        return 0

    with ProcessPoolExecutor(min(args.workers, len(TUNING))) as ex:
        fut_c = ex.map(run_symbol_c, [(s, False) for s in TUNING])
        print("Arm A (2024-2025, synthetic)")
        a = arm_synthetic("A")
        print("Arm B (09-21 grid, synthetic)")
        b = arm_synthetic("B")
        per = list(fut_c)

    c = {}
    for w in ("primary", "confirmation"):
        tr = [t for r in per for t in r["windows"][w]]
        s = summarise_rows([t[:6] for t in tr])
        s["inert_share"] = sum(t[6] for t in tr) / len(tr) if tr else None
        btc = next(r for r in per if r["symbol"] == "BTC")["windows"][w]
        s["btc"] = summarise_rows([t[:6] for t in btc])
        c[w] = s
    fid = {w: (c[w]["n"], round(c[w]["base_mean"], 3)) for w in c}
    fid_ok = all(fid[w] == FIDELITY[w] for w in FIDELITY)
    c_holds = all(c[w]["mean_diff"] > 0 for w in c)

    if not fid_ok:
        verdict = "DISCARDED"
    elif a["pooled_le_zero"] or b["pooled_le_zero"]:
        verdict = "FAIL"
    elif a["meets_criteria"] and b["meets_criteria"] and c_holds:
        verdict = "PASS"
    else:
        verdict = "INCONCLUSIVE"

    for name, arm in (("A", a), ("B", b)):
        p = arm["pooled"]
        print(f"\nArm {name}: cells favouring {arm['cells_favouring']}/{arm['cells_total']} "
              f"(need {MIN_CELLS[name]}), pooled diff {p['mean_diff']:+.4f}pp (need ≥ +{MIN_EFFECT}) "
              f"n={p['n']} n_eff={p['n_eff']}  base {p['base_mean']:+.3f} cand {p['cand_mean']:+.3f}  "
              f"TP1 hit {p['tp1_hit_base']:.0%}→{p['tp1_hit_cand']:.0%}  unresolved {p['unresolved_base']}/{p['unresolved_cand']}"
              f"  → {'meets' if arm['meets_criteria'] else 'does not meet'}"
              f"\n  BTC only: diff {arm['btc']['mean_diff']:+.4f}pp (n={arm['btc']['n']})")
    for w, s in c.items():
        print(f"Arm C {w}: n={s['n']} base {s['base_mean']:+.3f} cand {s['cand_mean']:+.3f} "
              f"diff {s['mean_diff']:+.4f}pp  TP1 hit {s['tp1_hit_base']:.0%}→{s['tp1_hit_cand']:.0%}  "
              f"inert {s['inert_share']:.0%}  BTC diff {s['btc']['mean_diff']:+.4f} (n={s['btc']['n']})")
    print(f"Arm C fidelity {fid} expected {FIDELITY} → {'ok' if fid_ok else 'MISMATCH'};  C holds: {c_holds}")
    print(f"\nH-TP: {verdict}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"prereg": "2026-10-15-tp1-distance-prereg.md",
                                        "verdict": verdict, "A": a, "B": b, "C": c,
                                        "fidelity": fid, "fidelity_expected": FIDELITY,
                                        "c_holds": c_holds}, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
