#!/usr/bin/env python3
"""Score H-RG exactly as pre-registered — `docs/superpowers/specs/2026-10-09-regime-gate-prereg.md`.

Does the spot regime gate (`regime_bearish` live, `regime_counter` in
`backtest._failing_gates`: no BUY while the regime is TRENDING/VOLATILE with ADX
trend_dir BEARISH) refuse entries worth having? The engine runs as deployed (spot
anti-chase vetoes off) on the sibling project's OKX 4h cache. NORMAL+ BUYs blocked by
this gate ALONE are compared with BUYs that pass every gate and with random entries in
the same regime, all through the live spot exit.

    ./venv/bin/python scripts/regime_ic.py --out docs/superpowers/specs/2026-10-09-regime-gate-run/result.json
    ./venv/bin/python scripts/regime_ic.py --smoke     # counts only, first 400 candles of BTC
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, RESOLVED, _failing_gates, _htf_at, _simulate_forward  # noqa: E402
from config import SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import TUNING, build_htf, eval_start, load_symbol  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from signals.engine import generate_signals  # noqa: E402

SPLIT, END = pd.Timestamp("2025-08-30"), pd.Timestamp("2026-08-30")
MAX_HOLD = MAX_HOLD_CANDLES["4h"]
MIN_N = {"primary": 30, "confirmation": 10}
SEEDS, N_BOOT, SEED = 20, 5000, 2026
GATE = "regime_counter"


def _window(ts):
    return "primary" if ts < SPLIT else ("confirmation" if ts < END else None)


def _bear(reg):
    return (reg or {}).get("regime") in ("TRENDING", "VOLATILE") and (reg or {}).get("trend_dir") == "BEARISH"


def run_symbol(args):
    """Score every candle of one symbol. Returns per-window arm P&Ls and counts."""
    sym, smoke = args
    logging.disable(logging.WARNING)
    df = load_symbol(sym, end=str(END.date()))
    frames = build_htf(df)
    start = eval_start(df, frames, None, "strict")
    stop = len(df) - 1
    if smoke:
        stop = min(stop, start + 400)
    buys, bear_idx, mismatch, gate_combos = [], [], 0, {}
    for i in range(start, stop):
        htf = _htf_at(frames, df.index[i]) if frames else None
        sig = generate_signals(df.iloc[: i + 1], htf, None, None, mode="spot",
                               threshold_override=SPOT_THRESHOLD)
        bear = _bear(sig.get("_regime"))
        if bear:
            bear_idx.append(i)
        if sig["type"] != "BUY" or sig.get("confidence") not in ("NORMAL", "STRONG"):
            continue
        gates = [g for g in _failing_gates(sig, "spot", df.iloc[: i + 1], None)
                 if g != "confidence_first"]
        if GATE in gates and not bear:
            mismatch += 1
        if GATE in gates:
            key = "+".join(sorted(gates))
            gate_combos[key] = gate_combos.get(key, 0) + 1
        buys.append((i, gates, sig))
    out = {"symbol": sym, "buys": len(buys), "mismatch": mismatch, "gate_combos": gate_combos,
           "windows": {}}
    if smoke:
        out["regime_only_n"] = sum(1 for _, g, _ in buys if g == [GATE])
        out["kept_n"] = sum(1 for _, g, _ in buys if not g)
        return out

    def sim(entries, dedupe):
        res, open_until = [], -1
        for i, sig in sorted(entries, key=lambda e: e[0]):
            if dedupe and i <= open_until:
                continue
            t = _simulate_forward(df, i, sig, MAX_HOLD, "4h", "spot")
            if t is None:
                continue
            if dedupe:
                open_until = i + t["candles_held"]
            if t["outcome"] in RESOLVED:
                res.append(float(t["pnl_pct"]))
        return res

    for w in ("primary", "confirmation"):
        inw = lambda i: _window(df.index[i]) == w
        only = sim([(i, s) for i, g, s in buys if g == [GATE] and inw(i)], True)
        kept = sim([(i, s) for i, g, s in buys if not g and inw(i)], True)
        pool = [i for i in bear_idx if inw(i)]
        rnd = []
        for sd in range(SEEDS):
            pick = random.Random(SEED + sd).sample(pool, min(len(only), len(pool))) if only else []
            sigs = [(i, _signal_at(df, i)) for i in pick if df.iloc[i]["ATR_14"] > 0]
            rnd += sim(sigs, False)
        out["windows"][w] = {"regime_only": only, "kept": kept, "random": rnd}
    return out


def summarise(p):
    a = np.asarray(p, float)
    if not len(a):
        return {"n": 0, "mean": None, "ci90": None}
    boot = np.random.default_rng(SEED).choice(a, (N_BOOT, len(a))).mean(1)
    return {"n": int(len(a)), "mean": float(a.mean()),
            "ci90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]}


def diff_ci(kept, only):
    k, o = np.asarray(kept, float), np.asarray(only, float)
    rng = np.random.default_rng(SEED)
    boot = rng.choice(k, (N_BOOT, len(k))).mean(1) - rng.choice(o, (N_BOOT, len(o))).mean(1)
    return float(k.mean() - o.mean()), [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    syms = ["BTC"] if args.smoke else TUNING
    with ProcessPoolExecutor(min(args.workers, len(syms))) as ex:
        per = list(ex.map(run_symbol, [(s, args.smoke) for s in syms]))
    if args.smoke:
        for r in per:
            print(f"smoke {r['symbol']}: NORMAL+ BUYs {r['buys']}, regime_only {r['regime_only_n']}, "
                  f"kept {r['kept_n']}, mismatch {r['mismatch']}, combos {r['gate_combos']}")
        return 0

    mism = sum(r["mismatch"] for r in per)
    assert mism == 0, f"{mism} regime_counter failures outside a bearish regime — discard"
    result = {"engine": "as deployed (316e0dc config)", "symbols": syms, "windows": {},
              "gate_combos": {r["symbol"]: r["gate_combos"] for r in per},
              "buys_normal_plus": {r["symbol"]: r["buys"] for r in per}}
    verdicts = {}
    for w in ("primary", "confirmation"):
        only = [p for r in per for p in r["windows"][w]["regime_only"]]
        kept = [p for r in per for p in r["windows"][w]["kept"]]
        rnd = [p for r in per for p in r["windows"][w]["random"]]
        so, sk = summarise(only), summarise(kept)
        rnd_mean = float(np.mean(rnd)) if rnd else float("nan")
        if so["n"] < MIN_N[w] or sk["n"] == 0:
            v1, d, ci = "INCONCLUSIVE", None, None
        else:
            d, ci = diff_ci(kept, only)
            v1 = "FAIL" if d <= 0 else ("PASS" if ci[0] > 0 else "INCONCLUSIVE")
        if so["n"] < MIN_N[w]:
            v2 = "INCONCLUSIVE"
        elif so["mean"] <= rnd_mean:
            v2 = "FAIL"
        else:
            v2 = "PASS" if not (so["ci90"][0] <= rnd_mean <= so["ci90"][1]) else "INCONCLUSIVE"
        btc = next(r for r in per if r["symbol"] == "BTC")["windows"][w]
        verdicts[w] = v1
        result["windows"][w] = {"regime_only": so, "kept": sk, "random_regime_mean": rnd_mean,
                                "random_n": len(rnd), "kept_minus_only": d, "ci90": ci,
                                "H-RG1": v1, "H-RG2": v2,
                                "btc": {"regime_only": summarise(btc["regime_only"]),
                                        "kept": summarise(btc["kept"])},
                                "per_symbol_regime_only_mean": {
                                    r["symbol"]: (round(float(np.mean(r["windows"][w]["regime_only"])), 3)
                                                  if r["windows"][w]["regime_only"] else None)
                                    for r in per}}
    p, c = verdicts["primary"], verdicts["confirmation"]
    overall = "FAIL" if p == "FAIL" and c != "PASS" else ("PASS" if p == c == "PASS" else "INCONCLUSIVE")
    result["overall"] = overall

    fmt = lambda s: "—" if s["mean"] is None else f"n={s['n']:4d} mean={s['mean']:+.3f}pp [{s['ci90'][0]:+.3f}, {s['ci90'][1]:+.3f}]"
    print(f"H-RG  spot 4h, {len(syms)} symbols, engine as deployed\n")
    for w, r in result["windows"].items():
        print(f"== {w}")
        print(f"  regime_only  {fmt(r['regime_only'])}")
        print(f"  kept         {fmt(r['kept'])}")
        print(f"  random_regime mean={r['random_regime_mean']:+.3f}pp (n={r['random_n']})")
        dd = "—" if r["kept_minus_only"] is None else f"{r['kept_minus_only']:+.3f} [{r['ci90'][0]:+.3f}, {r['ci90'][1]:+.3f}]"
        print(f"  kept − regime_only = {dd}   H-RG1 {r['H-RG1']}   H-RG2 {r['H-RG2']}")
        print(f"  BTC: regime_only {fmt(r['btc']['regime_only'])} · kept {fmt(r['btc']['kept'])}")
        print(f"  per symbol regime_only mean: {r['per_symbol_regime_only_mean']}")
    print(f"\nOVERALL (gate): {overall}")
    print("gate combos with regime_counter:", json.dumps(result["gate_combos"]))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
