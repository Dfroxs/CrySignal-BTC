#!/usr/bin/env python3
"""Score H-SR and H-PSY exactly as pre-registered: `docs/superpowers/specs/2026-10-10-sr-psy-gates-prereg.md`.

Do the spot gates `sr_first` (entry within 1x ATR below resistance) and `psy_sl_first`
(stop-loss just below a round number) refuse entries worth having? Each candle is scored
as live scores it (`backtest._score_candle`: 499-bar window, S/R passed to the engine).
NORMAL+ BUYs blocked by one gate ALONE are compared with BUYs that pass every gate and
with random bars where that gate's condition holds, all through the live spot exit.

    ./venv/bin/python scripts/spot_gates_ic.py --out docs/superpowers/specs/2026-10-10-sr-psy-gates-run/result.json
    ./venv/bin/python scripts/spot_gates_ic.py --smoke     # counts only, first 400 candles of BTC
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, RESOLVED, _failing_gates, _score_candle, _simulate_forward  # noqa: E402
from config import RISK_CONFIG, SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import TUNING, build_htf, eval_start, load_symbol  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from scripts.regime_ic import diff_ci, summarise  # noqa: E402

SPLIT, END = pd.Timestamp("2025-08-30"), pd.Timestamp("2026-08-30")
MAX_HOLD = MAX_HOLD_CANDLES["4h"]
MIN_N = {"primary": 30, "confirmation": 10}
SEEDS, SEED = 20, 2026
GATES = ("sr_first", "psy_sl_first")
_PYR = RISK_CONFIG["pyramid"]
PSY_BUFFER = _PYR.get("psychology_buffer_pct", 0.15)
SR_ATR = _PYR.get("sr_entry_risk_atr", 1.0)
IGNORED = ("confidence_first", "reentry_first")


def _window(ts):
    return "primary" if ts < SPLIT else ("confirmation" if ts < END else None)


def psy_step(sym, price):
    """BTC: live's $1,000. Others: round numbers of the price's own scale (prereg)."""
    if sym == "BTC":
        return _PYR.get("psychology_level_step", 1000)
    return 10 ** (math.floor(math.log10(price)) - 1)


def psy_hit(sl, step):
    """BUY stop within PSY_BUFFER % below the next round number: _failing_gates's rule."""
    if sl <= 0:
        return False
    next_round = (int(sl // step) + 1) * step
    return (next_round - sl) / sl * 100 <= PSY_BUFFER


def sr_hit(entry, atr, sr):
    res = (sr or {}).get("resistance")
    return bool(res and atr and 0 < res - entry <= atr * SR_ATR)


def gates_for(sym, sig, window):
    """Failing gates with the prereg's psychology rule. Also returns whether the replaced
    rule disagreed with _failing_gates's own (the BTC fidelity check)."""
    raw = [g for g in _failing_gates(sig, "spot", window, None) if g not in IGNORED]
    own = "psy_sl_first" in raw
    mine = psy_hit(sig.get("stop_loss", 0), psy_step(sym, sig["entry_price"]))
    gates = [g for g in raw if g != "psy_sl_first"] + (["psy_sl_first"] if mine else [])
    return gates, own != mine


def run_symbol(args):
    sym, smoke = args
    logging.disable(logging.WARNING)
    df = load_symbol(sym, end=str(END.date()))
    frames = build_htf(df)
    start = eval_start(df, frames, None, "strict")
    stop = min(len(df) - 1, start + 400) if smoke else len(df) - 1
    buys, cond, mismatch, sr_fires = [], {g: [] for g in GATES}, 0, 0
    for i in range(start, stop):
        window, sig = _score_candle(df, i, "4h", "spot", frames, SPOT_THRESHOLD, None)
        lv = _signal_at(df, i)
        if sr_hit(lv["entry_price"], lv["atr"], sig.get("support_resistance")):
            cond["sr_first"].append(i)
        if psy_hit(lv["stop_loss"], psy_step(sym, lv["entry_price"])):
            cond["psy_sl_first"].append(i)
        if sig["type"] != "BUY" or sig.get("confidence") not in ("NORMAL", "STRONG"):
            continue
        gates, differs = gates_for(sym, sig, window)
        mismatch += differs and sym == "BTC"
        sr_fires += "sr_first" in gates
        buys.append((i, gates, sig))
    out = {"symbol": sym, "buys": len(buys), "psy_mismatch_btc": mismatch, "sr_fires": sr_fires,
           "gate_combos": {}, "windows": {}}
    for _, g, _ in buys:
        k = "+".join(sorted(g)) or "(none)"
        out["gate_combos"][k] = out["gate_combos"].get(k, 0) + 1
    if smoke:
        out.update({f"only_{g}": sum(1 for _, gs, _ in buys if gs == [g]) for g in GATES})
        out["kept"] = sum(1 for _, gs, _ in buys if not gs)
        out["cond_pool"] = {g: len(v) for g, v in cond.items()}
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
        arms = {"kept": sim([(i, s) for i, g, s in buys if not g and inw(i)], True)}
        for gate in GATES:
            only_bars = [(i, s) for i, g, s in buys if g == [gate] and inw(i)]
            arms[f"only_{gate}"] = sim(only_bars, True)
            taken = {i for i, _ in only_bars}
            pool = [i for i in cond[gate] if inw(i) and i not in taken]
            n, rnd = len(arms[f"only_{gate}"]), []
            for sd in range(SEEDS):
                pick = random.Random(SEED + sd).sample(pool, min(n, len(pool))) if n else []
                rnd += sim([(i, _signal_at(df, i)) for i in pick if df.iloc[i]["ATR_14"] > 0], False)
            arms[f"random_{gate}"] = rnd
        out["windows"][w] = arms
    return out


def verdict1(only, kept, min_n):
    """H-x1 for one window. Returns (verdict, d, ci)."""
    if len(only) < min_n or not kept:
        return "INCONCLUSIVE", None, None
    d, ci = diff_ci(kept, only)
    return ("FAIL" if d <= 0 else ("PASS" if ci[0] > 0 else "INCONCLUSIVE")), d, ci


def verdict2(s, rnd_mean, min_n):
    if s["n"] < min_n:
        return "INCONCLUSIVE"
    if s["mean"] <= rnd_mean:
        return "FAIL"
    return "PASS" if not (s["ci90"][0] <= rnd_mean <= s["ci90"][1]) else "INCONCLUSIVE"


def overall(primary, confirmation):
    if primary == "FAIL" and confirmation != "PASS":
        return "FAIL"
    return "PASS" if primary == confirmation == "PASS" else "INCONCLUSIVE"


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
            print(f"smoke {r['symbol']}: NORMAL+ BUYs {r['buys']}, only_sr {r['only_sr_first']}, "
                  f"only_psy {r['only_psy_sl_first']}, kept {r['kept']}, sr_fires {r['sr_fires']}, "
                  f"psy mismatch {r['psy_mismatch_btc']}, cond pools {r['cond_pool']}\n"
                  f"  combos {r['gate_combos']}")
        return 0

    mism = sum(r["psy_mismatch_btc"] for r in per)
    fires = sum(r["sr_fires"] for r in per)
    result = {"engine": "as deployed, scored by backtest._score_candle", "symbols": syms,
              "psy_mismatch_btc": mism, "sr_fires": fires,
              "gate_combos": {r["symbol"]: r["gate_combos"] for r in per},
              "buys_normal_plus": {r["symbol"]: r["buys"] for r in per}, "gates": {}}
    if mism or not fires:
        result["overall"] = "DISCARDED"
        print(f"DISCARDED: BTC psychology mismatches {mism}, sr_first fires {fires}")
    else:
        for gate in GATES:
            res, vs = {}, {}
            for w in ("primary", "confirmation"):
                pool = lambda arm: [p for r in per for p in r["windows"][w][arm]]
                only, kept, rnd = pool(f"only_{gate}"), pool("kept"), pool(f"random_{gate}")
                so, sk = summarise(only), summarise(kept)
                rnd_mean = float(np.mean(rnd)) if rnd else float("nan")
                v1, d, ci = verdict1(only, kept, MIN_N[w])
                vs[w] = v1
                res[w] = {f"only_{gate}": so, "kept": sk, "random_mean": rnd_mean,
                          "random_n": len(rnd), "kept_minus_only": d, "ci90": ci, "H-x1": v1,
                          "H-x2": verdict2(so, rnd_mean, MIN_N[w]),
                          "btc_only": summarise(next(r for r in per if r["symbol"] == "BTC")
                                                ["windows"][w][f"only_{gate}"]),
                          "per_symbol_only_n": {r["symbol"]: len(r["windows"][w][f"only_{gate}"])
                                                for r in per}}
            res["overall"] = overall(vs["primary"], vs["confirmation"])
            result["gates"][gate] = res

    fmt = lambda s: "—" if s["mean"] is None else f"n={s['n']:4d} mean={s['mean']:+.3f}pp [{s['ci90'][0]:+.3f}, {s['ci90'][1]:+.3f}]"
    print(f"H-SR / H-PSY  spot 4h, {len(syms)} symbols, scored as live\n")
    for gate, res in result["gates"].items():
        print(f"######## {gate}")
        for w in ("primary", "confirmation"):
            r = res[w]
            dd = "—" if r["kept_minus_only"] is None else f"{r['kept_minus_only']:+.3f} [{r['ci90'][0]:+.3f}, {r['ci90'][1]:+.3f}]"
            print(f"== {w}\n  only    {fmt(r[f'only_{gate}'])}\n  kept    {fmt(r['kept'])}\n"
                  f"  random  mean={r['random_mean']:+.3f}pp (n={r['random_n']})\n"
                  f"  kept − only = {dd}   H-x1 {r['H-x1']}   H-x2 {r['H-x2']}\n"
                  f"  BTC only {fmt(r['btc_only'])}   per symbol n {r['per_symbol_only_n']}")
        print(f"OVERALL {gate}: {res['overall']}\n")
    print("gate combos:", json.dumps(result["gate_combos"]))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
