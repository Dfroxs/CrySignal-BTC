#!/usr/bin/env python3
"""Score H-TRL exactly as pre-registered: `docs/superpowers/specs/2026-11-10-futures-trail-prereg.md`.

Is futures' trail too wide? One candidate: `trailing_atr_factor` 2.5 (live 3.5), applied
through `_simulate_forward`'s own `exit_params`, everything else as live. Every entry runs
under both settings and the comparison is paired.

  A  synthetic BUY entries (exit_ic method), BTC ETH BNB XRP LINK, 2024-2025, 10 cells
  B  synthetic BUY entries, same assets, 2020-2023, 20 cells
  C  engine entries, every signal run_backtest simulates for BTC futures (guard):
     primary 2022-01-01 -> 2025-10-01 (contaminated), confirmation 2025-10-01 -> 2026-10-09

The arms, windows, criteria and discard checks are H-TPF's; the candidate-independent
helpers are imported from `scripts/tp1_futures_ic.py` rather than copied, so the two
instruments cannot drift. Only what depends on the candidate lives here.

    ./venv/bin/python scripts/trail_futures_ic.py --smoke     # counts only, no P&L
    ./venv/bin/python scripts/trail_futures_ic.py --out docs/superpowers/specs/2026-11-10-futures-trail-run/result.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import RESOLVED, _exit_signal, _simulate_forward  # noqa: E402
from scripts.exit_ic import _signal_at, synth_entries  # noqa: E402
from scripts.tp1_futures_ic import (  # noqa: E402
    ARM_YEARS, ASSETS, FID_TOL, HOLD, INERT_MAX, MIN_CELLS, MIN_EFFECT, MODE, STRIDE, TF,
    WINDOWS, arm_meets, fetch_cell, geometry_ok, pool_c, verdict,
)
from scripts.tp1_futures_ic import record_window as _record_window  # noqa: E402

RETRIES = 5
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "armc"
# Everything the recorded signals are computed from. A cache entry is valid only for the
# exact bytes of these files, so editing the engine, the simulator or the config can never
# serve a stale recording.
_CODE = ("backtest.py", "config.py", "scripts/tp1_futures_ic.py")
_CODE_DIRS = ("signals", "trading")


def code_hash():
    import hashlib
    h = hashlib.sha256()
    files = [ROOT / f for f in _CODE]
    for d in _CODE_DIRS:
        files += sorted((ROOT / d).rglob("*.py"))
    for f in files:
        h.update(str(f.relative_to(ROOT)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _fetch_window(start, end):
    """H-TPF's recorder, retried on a failed data fetch.

    A first scored run died on a `RequestTimeout` inside `run_backtest`'s own OHLCV fetch,
    after Arms A and B had finished and before any figure was summarised. The retry
    changes nothing that is computed: a failed fetch simply asks again, as `fetch_cell`
    already does for Arms A and B.
    """
    for attempt in range(RETRIES):
        try:
            return _record_window(start, end)
        except Exception as exc:  # noqa: BLE001 - a window is retried, never dropped
            logging.warning("record_window %s..%s attempt %d: %s", start, end, attempt + 1, exc)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"no data for window {start}..{end} after {RETRIES} tries")


def record_window(start, end):
    """(df, [(entry_idx, signal, own_trade)]) for one Arm C window, cached on disk.

    Every exit test replays the same two multi-year backtests (about 50 minutes) to get the
    same 625 + 178 signals. The recording is keyed by the window and a hash of the code it
    depends on (`code_hash`), so a changed engine, simulator or config re-records instead
    of reusing. Windows ending in the future are never cached: their data still moves.
    """
    import pickle
    import pandas as pd
    cacheable = pd.Timestamp(end) <= pd.Timestamp.now().normalize()
    f = CACHE_DIR / f"{start}_{end}_{code_hash()}.pkl"
    if cacheable and f.exists():
        try:
            with f.open("rb") as fh:
                return pickle.load(fh)
        except Exception as exc:  # noqa: BLE001 - a damaged cache file is re-recorded
            logging.warning("armc cache %s unreadable (%s); re-recording", f.name, exc)
    rec = _fetch_window(start, end)
    if cacheable and rec[0] is not None and rec[1]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(rec, fh)
        tmp.replace(f)
    return rec


CAND_TRAIL = 2.5                       # live futures: FUTURES_CONFIG["trailing_atr_factor"] = 3.5
CAND_PARAMS = {"trailing_atr_factor": CAND_TRAIL}


def excursion_giveback(df, i, t, sig):
    """Best favourable excursion over the trade's life minus its final P&L, in percent.

    The prereg reports give-back (never as a criterion) because it is what motivated the
    test. Read from the frame between entry and exit, so it needs nothing from the simulator.
    """
    held = int(t.get("candles_held") or 0)
    if held <= 0:
        return 0.0
    seg = df.iloc[i + 1: i + 1 + held]
    entry = float(sig["entry_price"])
    if sig["type"] == "BUY":
        best = (float(seg["high"].max()) - entry) / entry * 100
    else:
        best = (entry - float(seg["low"].min())) / entry * 100
    return max(best, 0.0) - float(t["pnl_pct"])


def _run(df, i, sig, params=None):
    """(pnl, resolved, time_exit, giveback) for one entry, on a fresh copy of `sig`."""
    s = dict(sig)
    t = _simulate_forward(df, i, s, HOLD, TF, MODE, exit_params=params)
    if t is None:
        return 0.0, False, False, 0.0
    return (float(t["pnl_pct"]), t["outcome"] in RESOLVED, t["outcome"] == "TIME_EXIT",
            excursion_giveback(df, i, t, sig))


def make_row(df, i, sig):
    """(base, cand, base_resolved, cand_resolved, te_base, te_cand, gb_base, gb_cand)."""
    pb, rb, tb, gb = _run(df, i, sig)
    pc, rc, tc, gc = _run(df, i, sig, CAND_PARAMS)
    return (pb, pc, rb, rc, tb, tc, gb, gc)


def summarise_rows(rows):
    n = len(rows)
    diffs = [r[1] - r[0] for r in rows]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0   # noqa: E731
    return {"n": n, "n_eff": sum(1 for d in diffs if d != 0),
            "mean_diff": mean(diffs),
            "base_mean": mean([r[0] for r in rows]),
            "cand_mean": mean([r[1] for r in rows]),
            "win_base": mean([r[0] > 0 for r in rows]),
            "win_cand": mean([r[1] > 0 for r in rows]),
            "unresolved_base": sum(1 for r in rows if not r[2]),
            "unresolved_cand": sum(1 for r in rows if not r[3]),
            "time_exit_base": mean([r[4] for r in rows]),
            "time_exit_cand": mean([r[5] for r in rows]),
            "giveback_base": mean([r[6] for r in rows]),
            "giveback_cand": mean([r[7] for r in rows])}


def run_cell(symbol, year):
    df = fetch_cell(symbol, year)
    entries = synth_entries(df, STRIDE, tail=HOLD + 2)
    if not entries:
        raise ValueError(f"no synthetic entries for {symbol} {year}: len(df)={len(df)}")
    rows = []
    for i in entries:
        raw = _signal_at(df, i)
        if not (raw["atr"] > 0):
            rows.append((0.0, 0.0, False, False, False, False, 0.0, 0.0))
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
                  f"giveback {s['giveback_base']:.2f}->{s['giveback_cand']:.2f}", flush=True)
    return {"cells": cells, "pooled": summarise_rows(pooled), "btc": summarise_rows(btc_rows),
            "ex_btc": summarise_rows(other_rows),
            "cells_favouring": sum(1 for c in cells if c["mean_diff"] > 0),
            "cells_total": len(cells)}


def arm_c_window(name):
    df, calls = record_window(*WINDOWS[name])
    if df is None or not calls:
        raise RuntimeError(f"Arm C {name}: no signals recorded")
    rows, by_dir, zero, max_diff, compared = [], {"BUY": [], "SELL": []}, 0, 0.0, 0
    for i, sig, own in calls:
        row = make_row(df, i, sig)
        if own is not None:
            max_diff = max(max_diff, abs(row[0] - float(own["pnl_pct"])))
            compared += 1
        zero += row[1] == row[0]
        if row[2] and row[3]:
            rows.append(row)
            by_dir[sig["type"]].append(row[1] - row[0])
    s = summarise_rows(rows)
    s.update(signals_recorded=len(calls), inert_share=zero / len(calls),
             fidelity={"compared": compared, "max_abs_diff": max_diff,
                       "ok": compared == len(calls) and max_diff <= FID_TOL},
             by_dir={k: {"n": len(v), "mean_diff": sum(v) / len(v) if v else 0.0}
                     for k, v in by_dir.items()})
    return s, by_dir


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
              f"zero-diff={s['inert_share']:.0%}  fidelity max|d|={s['fidelity']['max_abs_diff']:.2e}",
              flush=True)
    c = pool_c(per_window, dirs)

    discards = []
    if not all(w["fidelity"]["ok"] for w in per_window.values()):
        discards.append("Arm C baseline replay != run_backtest's own P&L")
    inert_arms = [w for w, s in per_window.items() if s["inert_share"] > INERT_MAX]

    v = "DISCARDED" if discards else verdict(a, b, c)
    for arm, r in (("A", a), ("B", b)):
        p = r["pooled"]
        print(f"\n{arm}: cells favouring {r['cells_favouring']}/{r['cells_total']} "
              f"(need {MIN_CELLS[arm]}), pooled {p['mean_diff']:+.4f}pp (need >= {MIN_EFFECT:+.2f}), "
              f"BTC {r['btc']['mean_diff']:+.4f}pp, ex-BTC {r['ex_btc']['mean_diff']:+.4f}pp, "
              f"TIME_EXIT {p['time_exit_base']:.0%}->{p['time_exit_cand']:.0%}, "
              f"give-back {p['giveback_base']:.2f}->{p['giveback_cand']:.2f}pp")
    print("C by direction (pooled): " + "  ".join(
        f"{k} n={x['n']} {x['mean_diff']:+.4f}pp" for k, x in c["by_dir"].items()))
    if inert_arms:
        print(f"note: paired diff is zero on > {INERT_MAX:.0%} of Arm C entries in {inert_arms}")
    for d in discards:
        print(f"DISCARD: {d}")
    print(f"\nVERDICT: {v}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"verdict": v, "A": a, "B": b, "C": c, "discards": discards,
             "inert_over_90pct": inert_arms,
             "config": {"cand_trail": CAND_TRAIL, "stride": STRIDE, "min_effect": MIN_EFFECT,
                        "windows": WINDOWS, "hold": HOLD}}, indent=1))
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
