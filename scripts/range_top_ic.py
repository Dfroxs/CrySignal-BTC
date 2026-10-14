#!/usr/bin/env python3
"""Score H-RT exactly as pre-registered: `docs/superpowers/specs/2026-10-14-range-top-prereg.md`.

Do spot BUYs bought at the top of their recent range lose? Run 3's three spot losses
(#5-#7, 2026-10-11..14) all bought the upper 72-90% of a 72h chop range on a "BB squeeze"
breakout coil. The universe is H-SR's `kept` arm (`spot_gates_ic`): NORMAL+ engine BUYs
that fail no gate, de-duplicated per symbol as live would take them, THEN split by flag.

  H-RT1  range position >= 0.70 over the 18 bars ending at the signal bar
  H-RT2  H-RT1 and the engine's "BB squeeze + above middle" and volume < 0.7x its 20-bar mean

    ./venv/bin/python scripts/range_top_ic.py --out docs/superpowers/specs/2026-10-14-range-top-run/result.json
    ./venv/bin/python scripts/range_top_ic.py --smoke     # counts only, first 400 candles of BTC
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import RESOLVED, _score_candle, _simulate_forward  # noqa: E402
from config import SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import TUNING, build_htf, eval_start, load_symbol  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from scripts.regime_ic import diff_ci, summarise  # noqa: E402
from scripts.spot_gates_ic import END, MAX_HOLD, MIN_N, SEED, SEEDS, _window, gates_for, overall  # noqa: E402

RANGE_BARS = 18          # 72h of 4h bars, signal bar included
TOP = 0.70
LOW_VOL = 0.7            # signals/engine.py condition 4
VOL_BARS = 20
FLAGS = ("top", "compound")
# H-SR's kept arm (2026-10-10-sr-psy-gates-results.md). The universe must reproduce it.
FIDELITY = {"primary": (347, -0.165), "confirmation": (43, -0.515)}
QUINTILES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0000001)


def bar_flags(df, i, sig, window):
    """(p, top, squeeze, low_vol) for bar i, from bars up to and including i only.
    p is None when the 18-bar range is flat (excluded and counted)."""
    seg = df.iloc[i - RANGE_BARS + 1: i + 1]
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    close = float(df.iloc[i]["close"])
    p = None if hi == lo else (close - lo) / (hi - lo)
    bb_buy = (sig.get("_contributions") or {}).get("bollinger", (0, 0))[0]
    squeeze = bool(bb_buy > 0 and close > float(window.iloc[-1]["BB_Middle"]))
    vavg = float(df.iloc[i - VOL_BARS + 1: i + 1]["volume"].mean())
    low_vol = bool(vavg > 0 and float(df.iloc[i]["volume"]) / vavg < LOW_VOL)
    top = p is not None and p >= TOP
    return {"p": p, "hi": hi, "top": top, "squeeze": squeeze, "low_vol": low_vol,
            "compound": top and squeeze and low_vol}


def run_symbol(args):
    sym, smoke = args
    logging.disable(logging.WARNING)
    df = load_symbol(sym, end=str(END.date()))
    frames = build_htf(df)
    start = max(eval_start(df, frames, None, "strict"), RANGE_BARS - 1, VOL_BARS - 1)
    stop = min(len(df) - 1, start + 400) if smoke else len(df) - 1
    kept, cond = [], {f: [] for f in FLAGS}
    for i in range(start, stop):
        window, sig = _score_candle(df, i, "4h", "spot", frames, SPOT_THRESHOLD, None)
        fl = bar_flags(df, i, sig, window)
        for f in FLAGS:
            if fl[f]:
                cond[f].append(i)
        if sig["type"] != "BUY" or sig.get("confidence") not in ("NORMAL", "STRONG"):
            continue
        gates, _ = gates_for(sym, sig, window)
        if not gates:
            kept.append((i, sig, fl))
    out = {"symbol": sym, "windows": {}}
    if smoke:
        out["kept_bars"] = len(kept)
        out["flagged_bars"] = {f: sum(1 for _, _, fl in kept if fl[f]) for f in FLAGS}
        out["squeeze_bars"] = sum(1 for _, _, fl in kept if fl["squeeze"])
        out["flat_range"] = sum(1 for _, _, fl in kept if fl["p"] is None)
        out["cond_pool"] = {f: len(v) for f, v in cond.items()}
        return out

    for w in ("primary", "confirmation"):
        # De-duplicate FIRST, exactly as spot_gates_ic.sim(kept, dedupe=True), then split.
        trades, open_until = [], -1
        for i, sig, fl in sorted((k for k in kept if _window(df.index[k[0]]) == w), key=lambda k: k[0]):
            if i <= open_until:
                continue
            t = _simulate_forward(df, i, sig, MAX_HOLD, "4h", "spot")
            if t is None:
                continue
            open_until = i + t["candles_held"]
            if t["outcome"] not in RESOLVED:
                continue
            atr = float(sig.get("atr") or df.iloc[i]["ATR_14"])
            fwd = df.iloc[i + 1: i + 1 + max(1, t["candles_held"])]
            mfe = (float(fwd["high"].max()) - sig["entry_price"]) / atr if atr > 0 and len(fwd) else None
            trades.append({"i": i, "pnl": float(t["pnl_pct"]), "p": fl["p"],
                           "top": fl["top"], "compound": fl["compound"], "squeeze": fl["squeeze"],
                           "low_vol": fl["low_vol"], "tp1_hit": bool(sig.get("_partial_taken")),
                           "tp1_above_range": sig["take_profit"] > fl["hi"], "mfe_atr": mfe})
        arms = {"trades": trades, "random": {}}
        for f in FLAGS:
            flagged = {t["i"] for t in trades if t[f]}
            n = len(flagged)
            pool = [i for i in cond[f] if _window(df.index[i]) == w and i not in flagged]
            rnd = []
            for sd in range(SEEDS):
                pick = random.Random(SEED + sd).sample(pool, min(n, len(pool))) if n else []
                for i in sorted(pick):
                    if df.iloc[i]["ATR_14"] <= 0:
                        continue
                    t = _simulate_forward(df, i, _signal_at(df, i), MAX_HOLD, "4h", "spot")
                    if t is not None and t["outcome"] in RESOLVED:
                        rnd.append(float(t["pnl_pct"]))
            arms["random"][f] = rnd
        out["windows"][w] = arms
    return out


def verdict(flagged, unflagged, min_n):
    """d = mean(unflagged) - mean(flagged); PASS = range-top BUYs are worse."""
    if len(flagged) < min_n or not unflagged:
        return "INCONCLUSIVE", None, None
    d, ci = diff_ci(unflagged, flagged)
    return ("FAIL" if d <= 0 else ("PASS" if ci[0] > 0 else "INCONCLUSIVE")), d, ci


def verdict_random(s, rnd_mean, min_n):
    """H-RT3, descriptive: PASS = the engine's flagged trades beat random flagged bars."""
    if s["n"] < min_n:
        return "INCONCLUSIVE"
    if s["mean"] <= rnd_mean:
        return "FAIL"
    return "PASS" if not (s["ci90"][0] <= rnd_mean <= s["ci90"][1]) else "INCONCLUSIVE"


def describe(ts):
    mfe = [t["mfe_atr"] for t in ts if t["mfe_atr"] is not None]
    return {"n": len(ts),
            "tp1_hit_rate": float(np.mean([t["tp1_hit"] for t in ts])) if ts else None,
            "tp1_above_range_share": float(np.mean([t["tp1_above_range"] for t in ts])) if ts else None,
            "mean_mfe_atr": float(np.mean(mfe)) if mfe else None}


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
            print(f"smoke {r['symbol']}: kept bars {r['kept_bars']}, flagged {r['flagged_bars']}, "
                  f"squeeze {r['squeeze_bars']}, flat range {r['flat_range']}, cond pools {r['cond_pool']}")
        return 0

    pooled = {w: [t for r in per for t in r["windows"][w]["trades"]] for w in FIDELITY}
    fid = {w: {"n": len(ts), "mean": round(float(np.mean([t["pnl"] for t in ts])), 3) if ts else None}
           for w, ts in pooled.items()}
    fid_ok = all(fid[w]["n"] == n and fid[w]["mean"] == m for w, (n, m) in FIDELITY.items())
    squeeze_fires = sum(t["squeeze"] for ts in pooled.values() for t in ts)
    result = {"prereg": "2026-10-14-range-top-prereg.md", "symbols": syms,
              "fidelity": fid, "fidelity_expected": FIDELITY, "squeeze_fires": squeeze_fires,
              "flat_range_excluded": {w: sum(t["p"] is None for t in ts) for w, ts in pooled.items()},
              "hypotheses": {}}
    if not fid_ok or not squeeze_fires:
        result["overall"] = "DISCARDED"
        print(f"DISCARDED: fidelity {fid} (expected {FIDELITY}), squeeze fires {squeeze_fires}")
    else:
        for f, name in (("top", "H-RT1"), ("compound", "H-RT2")):
            res, vs = {}, {}
            for w, ts in pooled.items():
                fl = [t for t in ts if t[f]]
                un = [t for t in ts if not t[f]]
                v, d, ci = verdict([t["pnl"] for t in fl], [t["pnl"] for t in un], MIN_N[w])
                vs[w] = v
                rnd = [x for r in per for x in r["windows"][w]["random"][f]]
                rnd_mean = float(np.mean(rnd)) if rnd else float("nan")
                sf = summarise([t["pnl"] for t in fl])
                btc = [t for t in next(r for r in per if r["symbol"] == "BTC")["windows"][w]["trades"]]
                res[w] = {"flagged": sf, "unflagged": summarise([t["pnl"] for t in un]),
                          "unflagged_minus_flagged": d, "ci90": ci, "verdict": v,
                          "random_mean": rnd_mean, "random_n": len(rnd),
                          "H-RT3": verdict_random(sf, rnd_mean, MIN_N[w]),
                          "describe_flagged": describe(fl), "describe_unflagged": describe(un),
                          "btc_flagged": summarise([t["pnl"] for t in btc if t[f]]),
                          "btc_unflagged": summarise([t["pnl"] for t in btc if not t[f]]),
                          "per_symbol_flagged_n": {r["symbol"]: sum(t[f] for t in r["windows"][w]["trades"])
                                                   for r in per}}
            res["overall"] = overall(vs["primary"], vs["confirmation"])
            result["hypotheses"][name] = res
        result["quintiles_not_verdict_bearing"] = {
            w: [{"p": f"{QUINTILES[k]:.1f}-{min(QUINTILES[k + 1], 1.0):.1f}",
                 **summarise([t["pnl"] for t in ts
                              if t["p"] is not None and QUINTILES[k] <= t["p"] < QUINTILES[k + 1]])}
                for k in range(5)]
            for w, ts in pooled.items()}

    fmt = lambda s: "—" if s["mean"] is None else f"n={s['n']:4d} mean={s['mean']:+.3f}pp [{s['ci90'][0]:+.3f}, {s['ci90'][1]:+.3f}]"
    print(f"H-RT  spot 4h, {len(syms)} symbols, scored as live")
    print(f"fidelity {fid} (expected {FIDELITY}), squeeze fires {squeeze_fires}\n")
    for name, res in result["hypotheses"].items():
        print(f"######## {name}")
        for w in ("primary", "confirmation"):
            r = res[w]
            dd = "—" if r["unflagged_minus_flagged"] is None else \
                f"{r['unflagged_minus_flagged']:+.3f} [{r['ci90'][0]:+.3f}, {r['ci90'][1]:+.3f}]"
            print(f"== {w}\n  flagged    {fmt(r['flagged'])}\n  unflagged  {fmt(r['unflagged'])}\n"
                  f"  random     mean={r['random_mean']:+.3f}pp (n={r['random_n']})\n"
                  f"  unflagged − flagged = {dd}   verdict {r['verdict']}   H-RT3 {r['H-RT3']}\n"
                  f"  describe flagged {r['describe_flagged']}\n  describe unflagged {r['describe_unflagged']}\n"
                  f"  BTC flagged {fmt(r['btc_flagged'])}   BTC unflagged {fmt(r['btc_unflagged'])}\n"
                  f"  per symbol flagged n {r['per_symbol_flagged_n']}")
        print(f"OVERALL {name}: {res['overall']}\n")
    for w, rows in result.get("quintiles_not_verdict_bearing", {}).items():
        print(f"quintiles ({w}, NOT verdict-bearing): " + "  ".join(f"{q['p']}: {fmt(q)}" for q in rows))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
