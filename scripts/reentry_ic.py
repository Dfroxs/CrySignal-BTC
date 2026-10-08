#!/usr/bin/env python3
"""Does the re-entry guard's anchor need an age limit? Pre-registered in
`docs/superpowers/specs/2026-10-09-reentry-age-prereg.md`.

The guard (`run_bot._check_reentry_quality`, mirrored by `backtest._failing_gates`)
compares a new entry with the last WIN/LOSS in the same direction and blocks it unless
price, confidence tier or strength has improved. The anchor never ages: in paper run 2 one
WIN at $77,361 from 2026-09-12 blocked every spot signal for three weeks while BTC sat at
$84k.

WHY THIS IS SEQUENTIAL, unlike entry_ic.py: the guard reads the last resolved trade, so it
only exists inside a sequence of trades. Each symbol is replayed the way
`backtest.run_backtest` replays one — one position at a time, the 2-candle same-side
cooldown, every Phase 3 gate via `_failing_gates`, exits via `_simulate_forward`. Both are
imported, not copied.

ARMS, on identical engine signals:
  unlimited   reentry_max_age_hours=None — the guard as it runs today
  aged        reentry_max_age_hours=--max-age

THE STATISTIC, in the unlimited arm: `kept − stale_rejected`.
  kept            every trade the unlimited arm took
  stale_rejected  every signal it blocked for re-entry ALONE (no other gate failing) with
                  an anchor older than --max-age, simulated forward as a shadow trade, one
                  shadow at a time
Those are exactly the entries an age limit would let through. The stale part of the guard
earns its place only if what it throws away is worse than what the system keeps.

DATA: Nakhoda's OKX 4h cache, its ten `tuning` symbols, strict 1D+1W EMA200 warmup. See
the pre-registration for which earlier experiments have read these candles.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import (  # noqa: E402
    MAX_HOLD_CANDLES, RESOLVED, _failing_gates, _htf_at, _simulate_forward,
)
from signals.engine import generate_signals  # noqa: E402
from signals.indicators import detect_support_resistance  # noqa: E402
from scripts.entry_ic import (  # noqa: E402
    NAKHODA_CACHE, TUNING, build_htf, eval_start, load_symbol, summarise,
)

logger = logging.getLogger(__name__)

COOLDOWN_4H = 2          # backtest.run_backtest's same-side cooldown on 4h


def engine_signals(df, htf_frames, start, threshold=None):
    """Every BUY the engine fires from `start`, scored exactly as backtest.py scores it:
    closed bars, real HTF, S/R from the window, market structure NEUTRAL."""
    from config import SPOT_THRESHOLD
    thr = SPOT_THRESHOLD if threshold is None else threshold
    out = []
    for i in range(start, len(df)):
        window = df.iloc[: i + 1]
        htf = _htf_at(htf_frames, df.index[i]) if htf_frames else None
        sig = generate_signals(window, htf=htf, market_structure=None,
                               sr=detect_support_resistance(window),
                               mode="spot", threshold_override=thr)
        if sig["type"] == "BUY":
            sig["mode"] = "spot"
            out.append((i, sig))
    return out


def simulate_sequence(index, signals, gate_fn, sim_fn, max_age_hours,
                      stale_after_hours=None, cooldown_n=COOLDOWN_4H):
    """Replay `signals` as one trade sequence, mirroring backtest.run_backtest.

    gate_fn(sig, i, last_resolved, max_age_hours) -> list of failing gate names
    sim_fn(i, sig) -> {"outcome", "pnl_pct", "candles_held"} or None

    Returns taken P&Ls, unresolved count, and — when `stale_after_hours` is given —
    the shadow P&Ls of signals blocked by re-entry alone on an anchor older than it.
    """
    taken, stale_rejected, trades, stale_trades = [], [], [], []
    unresolved = 0
    open_until = cooldown_until = cf_open_until = -1
    last_resolved = {}
    for i, sig in signals:
        if i <= open_until or i < cooldown_until:
            continue
        gates = gate_fn(sig, i, last_resolved, max_age_hours)
        if gates:
            prev = last_resolved.get(sig["type"])
            if (stale_after_hours is not None and gates == ["reentry_first"] and prev
                    and index[i] - prev[2] > pd.Timedelta(hours=stale_after_hours)
                    and i > cf_open_until):
                shadow = sim_fn(i, sig)
                if shadow:
                    cf_open_until = i + shadow["candles_held"]
                    if shadow["outcome"] in RESOLVED:
                        stale_rejected.append(float(shadow["pnl_pct"]))
                        stale_trades.append({**shadow, "i": i})
            continue
        trade = sim_fn(i, sig)
        if not trade:
            continue
        exit_idx = i + trade["candles_held"]
        open_until, cooldown_until = exit_idx, exit_idx + cooldown_n
        if trade["outcome"] in RESOLVED:
            taken.append(float(trade["pnl_pct"]))
            trades.append({**trade, "i": i})
        else:
            unresolved += 1
        if trade["outcome"] in ("WIN", "LOSS"):
            last_resolved[sig["type"]] = (sig["entry_price"], sig.get("strength", 0),
                                          index[min(exit_idx, len(index) - 1)])
    return {"taken": taken, "stale_rejected": stale_rejected, "unresolved": unresolved,
            "trades": trades, "stale_trades": stale_trades}


def diff_ci(a, b, seed=7, n_boot=2000):
    """90% bootstrap interval on mean(a) − mean(b), two independent samples."""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = (rng.choice(a, size=(n_boot, len(a))).mean(axis=1)
         - rng.choice(b, size=(n_boot, len(b))).mean(axis=1))
    return [float(np.percentile(d, 5)), float(np.percentile(d, 95))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=",".join(TUNING))
    ap.add_argument("--cache", type=Path, default=NAKHODA_CACHE)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-age", type=float, default=168.0, help="hours (default 168 = 7 days)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--trades-out", type=Path, default=None,
                    help="also write every trade record (both arms + shadows) as JSON lines")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s — %(message)s")

    max_hold = MAX_HOLD_CANDLES["4h"]
    pooled = {"kept": [], "stale_rejected": [], "aged": []}
    per_symbol, records = {}, []
    for base in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        df = load_symbol(base, args.cache, args.end)
        frames = build_htf(df)
        start = eval_start(df, frames, args.start, "strict")
        if start >= len(df) - 50:
            logger.warning("%s: HTF never warms up in this span — skipped", base)
            continue
        sigs = engine_signals(df, frames, start)

        def gate(sig, i, lr, age, _df=df):
            return _failing_gates(sig, "spot", _df.iloc[: i + 1], lr, reentry_max_age_hours=age)

        def sim(i, sig, _df=df):
            return _simulate_forward(_df, i, sig, max_hold, "4h", "spot")

        a = simulate_sequence(df.index, sigs, gate, sim, None, stale_after_hours=args.max_age)
        b = simulate_sequence(df.index, sigs, gate, sim, args.max_age)
        pooled["kept"].extend(a["taken"])
        pooled["stale_rejected"].extend(a["stale_rejected"])
        pooled["aged"].extend(b["taken"])
        for arm, rows in (("unlimited", a["trades"]), ("stale_shadow", a["stale_trades"]),
                          ("aged", b["trades"])):
            records.extend({**t, "symbol": base, "arm": arm} for t in rows)
        per_symbol[base] = {
            "span": [str(df.index[start].date()), str(df.index[-1].date())],
            "engine_buys": len(sigs),
            "kept": summarise(a["taken"]), "stale_rejected": summarise(a["stale_rejected"]),
            "aged": summarise(b["taken"]),
            "unresolved": {"unlimited": a["unresolved"], "aged": b["unresolved"]},
        }
        s = per_symbol[base]
        print(f"{base:6s} {s['span'][0]}→{s['span'][1]}  buys={len(sigs):4d}  "
              f"kept n={s['kept']['n']:3d} {s['kept']['mean']:+.3f}  "
              f"stale_rej n={s['stale_rejected']['n']:3d} {s['stale_rejected']['mean']:+.3f}  "
              f"aged n={s['aged']['n']:3d} {s['aged']['mean']:+.3f}", flush=True)

    p = {k: summarise(v) for k, v in pooled.items()}
    ci = diff_ci(pooled["kept"], pooled["stale_rejected"]) \
        if pooled["kept"] and pooled["stale_rejected"] else None
    breadth = [b for b, s in per_symbol.items()
               if s["stale_rejected"]["n"] >= 5 and s["kept"]["mean"] > s["stale_rejected"]["mean"]]
    eligible = [b for b, s in per_symbol.items() if s["stale_rejected"]["n"] >= 5]

    print("\n" + "=" * 72)
    for k in ("kept", "stale_rejected", "aged"):
        r = p[k]
        print(f"{k:15s} n={r['n']:5d}  mean={r['mean']:+.3f}pp  "
              f"90% CI [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]  win={r['win_rate']:.1f}%  "
              f"total={sum(pooled[k]):+.2f}pp")
    if ci:
        d = p["kept"]["mean"] - p["stale_rejected"]["mean"]
        print(f"\nkept − stale_rejected = {d:+.3f}pp   90% CI [{ci[0]:+.3f}, {ci[1]:+.3f}]")
    print(f"breadth (stale_rejected n≥5): kept > stale_rejected on "
          f"{len(breadth)}/{len(eligible)} symbols")
    print("=" * 72)

    if args.trades_out:
        args.trades_out.parent.mkdir(parents=True, exist_ok=True)
        with args.trades_out.open("w") as fh:
            for r in records:
                fh.write(json.dumps(r, default=str) + "\n")
        print(f"{len(records)} trade records → {args.trades_out}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "pooled": p, "kept_minus_stale_rejected_ci90": ci,
            "breadth": {"favour_guard": breadth, "eligible": eligible},
            "per_symbol": per_symbol,
            "config": {"symbols": args.symbols, "start": args.start, "end": args.end,
                       "max_age_hours": args.max_age, "max_hold": max_hold,
                       "cooldown": COOLDOWN_4H, "htf_warmup": "strict"},
        }, indent=1, default=str))
        print(f"written → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
