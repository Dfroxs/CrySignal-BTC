#!/usr/bin/env python3
"""Score H-CT exactly as pre-registered — `docs/superpowers/specs/2026-10-09-counter-trend-prereg.md`.

Does the counter-trend veto (`signals/engine.py`: no SELL while 1D is BULLISH, no BUY
while 1D is BEARISH) remove entries worth having? Futures 1h on BTCUSDT perp history,
HTF (4h + 1D) resampled from the 1h base. Every candle is scored by
`backtest._score_candle`, the live window and S/R included, with the veto on and off.
The signals only the veto removes (`ct_vetoed`) are compared with count-matched random
counter-trend entries through the live futures exit.

    ./venv/bin/python scripts/veto_ic.py \\
        --csv docs/superpowers/specs/2026-10-09-hist-futures-ic-run/perp_1h.csv \\
        --out docs/superpowers/specs/2026-10-09-counter-trend-run/result.json
"""
from __future__ import annotations

import argparse
import functools
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

import backtest  # noqa: E402
from backtest import (RESOLVED, _exit_signal, _failing_gates, _htf_at,  # noqa: E402
                      _live_htf_series, _live_indicators, _score_candle, _simulate_forward)
from config import RISK_CONFIG, SIGNAL_THRESHOLD  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402

EVAL_START = pd.Timestamp("2021-01-01")
WINDOWS = {"primary": ("2021-01-01", "2025-01-01"), "confirmation": ("2025-01-01", "2026-08-30")}
MIN_N = {"primary": 100, "confirmation": 30}
MAX_HOLD = backtest.MAX_HOLD_CANDLES["1h"]
SEEDS = 20
N_BOOT = 5000
SEED = 2026
OPPOSES = {"SELL": "BULLISH", "BUY": "BEARISH"}       # 1D trend that vetoes each side

_DF = _FRAMES = None                                   # per-worker globals


def load(csv: Path):
    raw = pd.read_csv(csv)
    raw.index = pd.to_datetime(raw["open_time"], unit="ms")
    raw.index.name = "timestamp"
    raw = raw[["open", "high", "low", "close", "volume"]].astype(float)
    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    frames = {}
    for tf, rule, delta in (("4h", "4h", pd.Timedelta(hours=4)), ("1d", "1D", pd.Timedelta(days=1))):
        d = raw.resample(rule).agg(ohlc).dropna()
        series = _live_htf_series(d)
        frames[tf] = (series, (series.index + delta).values)
    return raw, frames


def _init(cache):
    """Workers load the frames main computed once. The per-bar HTF series take minutes
    and would otherwise be rebuilt by every worker."""
    global _DF, _FRAMES
    logging.disable(logging.WARNING)
    _DF, _FRAMES = pd.read_pickle(cache)


def _score_chunk(idx):
    """Score candles with the counter-trend veto OFF; for each that fires, rescore with
    it ON. Returns (i, type, signal_off, type_on, ct_reason, failing_phase3_gates)."""
    real = backtest.generate_signals
    off = functools.partial(real, gates_disabled=("counter_trend",))
    out = []
    for i in idx:
        backtest.generate_signals = off
        try:
            window, sig = _score_candle(_DF, i, "1h", "futures", _FRAMES, SIGNAL_THRESHOLD, None)
        finally:
            backtest.generate_signals = real
        if sig["type"] == "HOLD":
            continue
        _, on = _score_candle(_DF, i, "1h", "futures", _FRAMES, SIGNAL_THRESHOLD, None)
        ct = any("Counter-trend block" in r for r in on.get("reasons", []))
        gates = _failing_gates(sig, "futures", window, None) if on["type"] == "HOLD" else []
        keep = {k: sig.get(k) for k in ("type", "entry_price", "stop_loss", "take_profit",
                                        "tp2", "atr", "strength", "confidence")}
        out.append((i, sig["type"], keep, on["type"], ct, gates))
    return out


def trend_1d(frames, ts):
    return (_htf_at(frames, ts) or {}).get("1d")


def sell_signal_at(df, i):
    """`exit_ic._signal_at` mirrored for a short: same ATR stop, same R:R."""
    row = df.iloc[i]
    atr = float(row["ATR_14"])
    entry = float(row["close"])
    risk = atr * RISK_CONFIG["atr_multiplier"]
    tp1 = entry - risk * RISK_CONFIG["take_profit_rr"]
    return {"type": "SELL", "entry_price": entry, "stop_loss": entry + risk,
            "take_profit": tp1, "tp2": entry - (entry - tp1) * 2, "atr": atr}


def simulate(dfi, entries, dedupe):
    """[(i, pnl)] for resolved entries through the live futures exit."""
    out, open_until = [], {"BUY": -1, "SELL": -1}
    for i, sig in sorted(entries, key=lambda e: e[0]):
        if dedupe and i <= open_until[sig["type"]]:
            continue
        t = _simulate_forward(dfi, i, _exit_signal(dict(sig, mode="futures"), "futures"),
                              MAX_HOLD, "1h", "futures")
        if t is None:
            continue
        if dedupe:
            open_until[sig["type"]] = i + t["candles_held"]
        if t["outcome"] in RESOLVED:
            out.append((i, float(t["pnl_pct"])))
    return out


def summarise(p, seed=SEED):
    a = np.asarray(p, float)
    if not len(a):
        return {"n": 0, "mean": None, "ci90": None, "win": None}
    boot = np.random.default_rng(seed).choice(a, (N_BOOT, len(a))).mean(1)
    return {"n": int(len(a)), "mean": float(a.mean()),
            "ci90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))],
            "win": float((a > 0).mean() * 100)}


def verdicts(s, rnd_mean, min_n):
    if s["n"] < min_n or s["mean"] is None:
        return "INCONCLUSIVE", "INCONCLUSIVE"
    lo, hi = s["ci90"]
    if s["mean"] <= rnd_mean:
        ct1 = "FAIL"
    else:
        ct1 = "PASS" if not (lo <= rnd_mean <= hi) else "INCONCLUSIVE"
    ct2 = "PASS" if s["mean"] > 0 and lo > 0 else ("FAIL" if hi < 0 else "INCONCLUSIVE")
    return ct1, ct2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--smoke", action="store_true",
                    help="instrument check on 2020-09-01 → 09-15 only, OUTSIDE both windows; no verdicts")
    args = ap.parse_args()
    logging.disable(logging.WARNING)

    raw, frames = load(args.csv)
    dfi = _live_indicators(raw, backtest._vwap_period("1h"))
    first_real_1d = frames["1d"][0].index[199] + pd.Timedelta(days=1)
    assert first_real_1d < EVAL_START, f"1D EMA200 not real until {first_real_1d} — discard"
    lo = int(raw.index.searchsorted(EVAL_START))
    hi = len(raw) - MAX_HOLD - 1
    if args.smoke:
        lo, hi = (int(raw.index.searchsorted(pd.Timestamp(t))) for t in ("2020-09-01", "2020-09-15"))
    idx = list(range(lo, hi))
    chunks = [idx[k::args.workers * 8] for k in range(args.workers * 8)]
    print(f"H-CT  BTCUSDT perp 1h  scoring {len(idx)} candles "
          f"{raw.index[lo]} → {raw.index[hi - 1]} on {args.workers} workers", flush=True)
    import tempfile
    cache = Path(tempfile.mkdtemp()) / "frames.pkl"
    pd.to_pickle((raw, frames), cache)
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(str(cache),)) as ex:
        fired = [r for part in ex.map(_score_chunk, chunks) for r in part]
    fired.sort(key=lambda r: r[0])

    # Discard check: the veto must actually hold on the configured engine.
    trend = {i: trend_1d(frames, raw.index[i]) for i, *_ in fired}
    leaks = [i for i, t, _, t_on, *_ in fired if t_on != "HOLD" and trend[i] == OPPOSES[t_on]]
    assert not leaks, f"configured engine fired against the 1D trend at {leaks[:5]} — discard"

    vetoed = [(i, s) for i, t, s, t_on, ct, g in fired if t_on == "HOLD"]
    if args.smoke:
        print(f"smoke: fired with veto off {len(fired)}, vetoed {len(vetoed)}, "
              f"kept {len(fired) - len(vetoed)}; checks passed")
        print("  sample:", [(str(raw.index[i]), t, t_on, ct, g) for i, t, s, t_on, ct, g in fired[:4]])
        v = simulate(dfi, vetoed, True)
        print(f"  simulated vetoed entries resolved: {len(v)}")
        return 0
    not_ct = [i for i, t, s, t_on, ct, g in fired if t_on == "HOLD" and not ct]
    assert not not_ct, f"HOLD without the counter-trend line at {not_ct[:5]}"
    kept = [(i, s) for i, t, s, t_on, ct, g in fired if t_on != "HOLD"]
    gates_of = {i: g for i, t, s, t_on, ct, g in fired if t_on == "HOLD"}

    result = {"data": str(args.csv), "candles": len(idx), "fired_ct_off": len(fired),
              "windows": {}}
    eligible = {d: [i for i in idx if trend_1d(frames, raw.index[i]) == OPPOSES[d]]
                for d in ("SELL", "BUY")}
    for wname, (ws, we) in WINDOWS.items():
        in_w = lambda i: pd.Timestamp(ws) <= raw.index[i] < pd.Timestamp(we)
        wres = {}
        for d in ("SELL", "BUY"):
            v = simulate(dfi, [(i, s) for i, s in vetoed if s["type"] == d and in_w(i)], True)
            k = simulate(dfi, [(i, s) for i, s in kept if s["type"] == d and in_w(i)], True)
            pool = [i for i in eligible[d] if in_w(i)]
            rnd = []
            for sd in range(SEEDS):
                pick = random.Random(SEED + sd).sample(pool, min(len(v), len(pool))) if v else []
                sigs = [(i, sell_signal_at(dfi, i) if d == "SELL" else _signal_at(dfi, i))
                        for i in pick if dfi.iloc[i]["ATR_14"] > 0]
                rnd += [p for _, p in simulate(dfi, sigs, False)]
            rnd_mean = float(np.mean(rnd)) if rnd else float("nan")
            sv = summarise([p for _, p in v])
            ct1, ct2 = verdicts(sv, rnd_mean, MIN_N[wname])
            phase3 = sum(1 for i, _ in v if {"regime_counter", "trend_confluence"} & set(gates_of.get(i, [])))
            years = {}
            for i, p in v:
                years.setdefault(raw.index[i].year, []).append(p)
            wres[d] = {"ct_vetoed": sv, "random_ct_mean": rnd_mean, "random_ct_n": len(rnd),
                       "kept": summarise([p for _, p in k]),
                       "H-CT1": ct1, "H-CT2": ct2,
                       "also_fail_phase3_trend_gates": phase3,
                       "per_year_mean": {y: round(float(np.mean(ps)), 4) for y, ps in sorted(years.items())}}
        result["windows"][wname] = wres

    for wname, wres in result["windows"].items():
        print(f"\n== {wname} {WINDOWS[wname][0]} → {WINDOWS[wname][1]}")
        for d, r in wres.items():
            label = "SELL in 1D BULLISH (primary question)" if d == "SELL" else "BUY in 1D BEARISH (secondary)"
            s, k = r["ct_vetoed"], r["kept"]
            fmt = lambda x: "—" if x["mean"] is None else (
                f"n={x['n']:4d} mean={x['mean']:+.3f}pp [{x['ci90'][0]:+.3f}, {x['ci90'][1]:+.3f}] win {x['win']:.0f}%")
            print(f"  {label}")
            print(f"    ct_vetoed  {fmt(s)}")
            print(f"    random_ct  mean={r['random_ct_mean']:+.3f}pp (n={r['random_ct_n']}, {SEEDS} seeds)")
            print(f"    kept (with-trend, same side)  {fmt(k)}")
            print(f"    H-CT1 {r['H-CT1']}   H-CT2 {r['H-CT2']}   "
                  f"also blocked by regime_counter/trend_confluence: {r['also_fail_phase3_trend_gates']}/{s['n']}")
            print(f"    per year: {r['per_year_mean']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
