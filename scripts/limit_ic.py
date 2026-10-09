#!/usr/bin/env python3
"""Score H-LO exactly as pre-registered — `docs/superpowers/specs/2026-10-10-limit-entry-prereg.md`.

Would a post-only limit at the signal price, resting for one bar, beat the market entry
on futures? Every market-arm entry (engine as deployed, live futures exit) is paired with
its limit twin: filled if the next bar trades 1bp through the price and does not also reach
TP1 (ambiguous order counts as unfilled), and then it earns the market P&L plus the entry-leg
saving; unfilled earns 0.

    ./venv/bin/python scripts/limit_ic.py \\
        --csv docs/superpowers/specs/2026-10-09-hist-futures-ic-run/perp_1h.csv \\
        --out docs/superpowers/specs/2026-10-10-limit-entry-run/result.json
    ./venv/bin/python scripts/limit_ic.py --csv ... --smoke   # 2020-09 only, outside both windows
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest  # noqa: E402
from backtest import (RESOLVED, _exit_signal, _failing_gates, _live_indicators,  # noqa: E402
                      _score_candle, _simulate_forward)
from config import EXECUTION_CONFIG, SIGNAL_THRESHOLD  # noqa: E402
from scripts.veto_ic import EVAL_START, MAX_HOLD, N_BOOT, SEED, WINDOWS, load  # noqa: E402

MIN_N = {"primary": 100, "confirmation": 30}
THROUGH = 0.0001                     # 1bp through the limit: a touch is not a fill
MAKER = 0.02                         # Binance USDⓈ-M regular-tier maker fee (%)


def saving(taker):
    return taker + EXECUTION_CONFIG["slippage_pct"] - MAKER


S = saving(EXECUTION_CONFIG["futures_fee_pct"])
S_ALT = saving(0.05)

_DF = _FRAMES = None


def _init(cache):
    global _DF, _FRAMES
    logging.disable(logging.WARNING)
    _DF, _FRAMES = pd.read_pickle(cache)


def _score_chunk(idx):
    """(i, signal fields, failing Phase 3 gates) for every candle the deployed engine fires."""
    out = []
    for i in idx:
        window, sig = _score_candle(_DF, i, "1h", "futures", _FRAMES, SIGNAL_THRESHOLD, None)
        if sig["type"] == "HOLD":
            continue
        keep = {k: sig.get(k) for k in ("type", "entry_price", "stop_loss", "take_profit",
                                        "tp2", "atr", "strength", "confidence")}
        out.append((i, keep, _failing_gates(sig, "futures", window, None)))
    return out


def market_arm(dfi, fired):
    """De-duplicated per direction, as veto_ic.simulate. [(i, exit-signal, m_pnl, gates)]."""
    out, open_until = [], {"BUY": -1, "SELL": -1}
    for i, sig, gates in fired:
        if i <= open_until[sig["type"]]:
            continue
        es = _exit_signal(dict(sig, mode="futures"), "futures")
        t = _simulate_forward(dfi, i, es, MAX_HOLD, "1h", "futures")
        if t is None:
            continue
        open_until[sig["type"]] = i + t["candles_held"]
        if t["outcome"] in RESOLVED:
            out.append((i, es, float(t["pnl_pct"]), gates))
    return out


def limit_twin(dfi, i, es):
    """('filled' | 'unfilled' | 'ambiguous'), reading bar i+1 only."""
    nxt = dfi.iloc[i + 1]
    L, tp = float(es["entry_price"]), float(es["take_profit"])
    if es["type"] == "BUY":
        through, hits_tp = nxt["low"] <= L * (1 - THROUGH), nxt["high"] >= tp
    else:
        through, hits_tp = nxt["high"] >= L * (1 + THROUGH), nxt["low"] <= tp
    if not through:
        return "unfilled"
    return "ambiguous" if hits_tp else "filled"


def boot(a):
    a = np.asarray(a, float)
    b = np.random.default_rng(SEED).choice(a, (N_BOOT, len(a))).mean(1)
    return [float(np.percentile(b, 5)), float(np.percentile(b, 95))]


def mean(a):
    return float(np.mean(a)) if len(a) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--smoke", action="store_true",
                    help="instrument check on 2020-09-01 → 10-01 only, OUTSIDE both windows; no verdicts")
    args = ap.parse_args()
    logging.disable(logging.WARNING)

    raw, frames = load(args.csv)
    dfi = _live_indicators(raw, backtest._vwap_period("1h"))
    lo = int(raw.index.searchsorted(EVAL_START))
    hi = len(raw) - MAX_HOLD - 1
    if args.smoke:
        lo, hi = (int(raw.index.searchsorted(pd.Timestamp(t))) for t in ("2020-09-01", "2020-10-01"))
    idx = list(range(lo, hi))
    chunks = [idx[k::args.workers * 8] for k in range(args.workers * 8)]
    print(f"H-LO  BTCUSDT perp 1h  scoring {len(idx)} candles "
          f"{raw.index[lo]} → {raw.index[hi - 1]} on {args.workers} workers", flush=True)
    cache = Path(tempfile.mkdtemp()) / "frames.pkl"
    pd.to_pickle((raw, frames), cache)
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(str(cache),)) as ex:
        fired = sorted((r for part in ex.map(_score_chunk, chunks) for r in part),
                       key=lambda r: r[0])

    rows = []
    for i, es, m, gates in market_arm(dfi, fired):
        state = limit_twin(dfi, i, es)
        filled = state == "filled"
        l = m + S if filled else 0.0
        if filled:
            assert abs((l - m) - S) < 1e-9, "filled l − m ≠ s — discard"
        rows.append({"i": i, "ts": raw.index[i], "type": es["type"], "state": state, "m": m,
                     "d": l - m, "d_alt": (m + S_ALT if filled else 0.0) - m,
                     "phase3_pass": not gates})
    if args.smoke:
        st = pd.Series([r["state"] for r in rows]).value_counts().to_dict()
        print(f"smoke: fired {len(fired)}, market entries resolved {len(rows)}, states {st}; "
              f"s={S:.3f} s_alt={S_ALT:.3f}; checks passed")
        return 0

    result = {"data": str(args.csv), "candles": len(idx), "fired": len(fired),
              "s": S, "s_alt": S_ALT, "windows": {}}
    verdict = {}
    for w, (a, b) in WINDOWS.items():
        R = [r for r in rows if pd.Timestamp(a) <= r["ts"] < pd.Timestamp(b)]
        d = [r["d"] for r in R]
        n = len(R)
        dbar = mean(d)
        ci = boot(d) if n else None
        if n < MIN_N[w]:
            v = "INCONCLUSIVE"
        elif dbar <= 0:
            v = "FAIL"
        else:
            v = "PASS" if ci[0] > 0 else "INCONCLUSIVE"
        verdict[w] = (v, dbar)
        states = pd.Series([r["state"] for r in R]).value_counts().to_dict()
        by = lambda f: {k: (round(mean([r["d"] for r in R if f(r) == k]), 4),
                            sum(1 for r in R if f(r) == k))
                        for k in sorted({f(r) for r in R})}
        result["windows"][w] = {
            "n": n, "d_mean": dbar, "ci90": ci, "H-LO1": v,
            "fill_rate": states.get("filled", 0) / n if n else None, "states": states,
            "m_all": mean([r["m"] for r in R]),
            "m_filled": mean([r["m"] for r in R if r["state"] == "filled"]),
            "m_unfilled": mean([r["m"] for r in R if r["state"] == "unfilled"]),
            "m_ambiguous": mean([r["m"] for r in R if r["state"] == "ambiguous"]),
            "d_mean_taker_0.05": mean([r["d_alt"] for r in R]),
            "d_by_direction": by(lambda r: r["type"]),
            "d_phase3_pass": by(lambda r: r["phase3_pass"]),
            "d_by_year": by(lambda r: r["ts"].year),
        }
    (pv, _), (_, cd) = verdict["primary"], verdict["confirmation"]
    overall = ("PASS" if pv == "PASS" and cd is not None and cd > 0
               else "FAIL" if pv == "FAIL" else "INCONCLUSIVE")
    result["overall"] = overall

    print(f"H-LO  s={S:.3f}pp (alt {S_ALT:.3f})  fired {len(fired)}\n")
    for w, r in result["windows"].items():
        print(f"== {w}  n={r['n']}  fill {r['fill_rate']:.1%}  states {r['states']}")
        print(f"  d̄ = {r['d_mean']:+.4f}pp  90% [{r['ci90'][0]:+.4f}, {r['ci90'][1]:+.4f}]  H-LO1 {r['H-LO1']}")
        print(f"  market m: all {r['m_all']:+.3f}  filled {r['m_filled']:+.3f}  "
              f"unfilled {r['m_unfilled']:+.3f}  ambiguous {r['m_ambiguous']}")
        print(f"  d̄ at taker 0.05: {r['d_mean_taker_0.05']:+.4f}")
        print(f"  by direction {r['d_by_direction']}  phase3 pass {r['d_phase3_pass']}")
        print(f"  by year {r['d_by_year']}")
    print(f"\nOVERALL: {overall}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
