#!/usr/bin/env python3
"""Is the 1.2x confidence gate throwing away money, or separating nothing?

THE DEFECT. The engine fires a BUY at `strength >= 1.0 x threshold`: a Telegram alert
goes out and a cycle_log row is written. Phase 3 opens a position only at
`strength >= 1.2 x threshold` (NORMAL confidence, signals/market_data.get_signal_confidence).
The band between them produces an alert, a row, and nothing else. Over paper run 1 it
swallowed 25 of 29 futures signals and futures opened zero positions in 35 days.

Worse, the adaptive controller counts signals that FIRE, not positions that OPEN
(`_update_threshold_state(signal['type'])`), so it raised the bar in response to activity
that never happened.

TWO QUESTIONS, deliberately separate:

  H1  Does the gate cost money?   Are dead-zone entries better than chance?
  H2  Does the ratio mean anything?  Do entries above 1.2x beat those below it?

They can both fail. That would say the bar is arbitrary but harmless — the dead zone is
then a reporting defect (alerts for signals that can never open) rather than a financial
one, and the fix is to stop firing in that band, not to widen it.

METHOD. Every engine BUY over the window is bucketed by `strength / threshold` and run
through the SAME exit simulator the bot uses, with the same costs. A count-matched random
baseline over several seeds gives the floor. Entries are simulated independently, because
CLAUDE.md records that `open_until` makes sequence comparisons here unreadable.

The threshold is FIXED at SPOT_THRESHOLD rather than adaptive. That is deliberate: the
live controller moves the bar, so a ratio measured against a moving threshold would
confound the gate with the controller. Fixing it isolates the gate.

Run:  ./venv/bin/python scripts/deadzone_ic.py --out docs/.../deadzone.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, _htf_at  # noqa: E402
from config import SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import (  # noqa: E402
    build_htf, entries_random, eval_start, load_symbol, run_entries, summarise,
)
from signals.engine import generate_signals  # noqa: E402

DEAD_LO, DEAD_HI = 1.0, 1.2          # fires here, never opens
STRONG = 1.5                          # get_signal_confidence's STRONG boundary


def bucket(ratio: float) -> str:
    if ratio < DEAD_HI:
        return "deadzone"
    if ratio < STRONG:
        return "normal"
    return "strong"


def classified_entries(df, frames, start, threshold):
    """Every engine BUY from `start`, tagged with its strength/threshold bucket."""
    out = {"deadzone": [], "normal": [], "strong": []}
    for i in range(start, len(df)):
        sig = generate_signals(df.iloc[: i + 1], _htf_at(frames, df.index[i]), None, None,
                               mode="spot", threshold_override=threshold)
        if sig["type"] != "BUY":
            continue
        out[bucket(sig["strength"] / threshold)].append((i, sig))
    return out


def ci_of_difference(a, b, seed=11, n=2000):
    """90% bootstrap interval on mean(a) - mean(b) for two independent samples."""
    if not a or not b:
        return None
    rng = np.random.default_rng(seed)
    x, y = np.asarray(a, float), np.asarray(b, float)
    d = (rng.choice(x, (n, len(x)), replace=True).mean(axis=1)
         - rng.choice(y, (n, len(y)), replace=True).mean(axis=1))
    return [float(np.percentile(d, 5)), float(np.percentile(d, 95))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,OKB,ICP,SUSHI,NEAR,UNI,DOGE")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default="2025-08-30")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    max_hold = MAX_HOLD_CANDLES["4h"]
    pooled = {k: [] for k in ("deadzone", "normal", "strong", "random")}
    per_symbol = {}

    for base in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        df = load_symbol(base, end=args.end)
        if len(df) < 300:
            continue
        frames = build_htf(df)
        start = eval_start(df, frames, args.start, "strict")
        if start >= len(df) - 50:
            print(f"{base:6s} HTF never warms up — skipped", flush=True)
            continue

        buckets = classified_entries(df, frames, start, SPOT_THRESHOLD)
        row = {}
        for k, ents in buckets.items():
            row[k], _ = run_entries(df, ents, max_hold)
            pooled[k].extend(row[k])

        n_sig = sum(len(v) for v in buckets.values())
        rnd = []
        for sd in range(args.seeds):
            p, _ = run_entries(df, entries_random(df, n_sig, seed=sd, lo=start), max_hold)
            rnd.extend(p)
        row["random"] = rnd
        pooled["random"].extend(rnd)

        per_symbol[base] = {k: summarise(v) for k, v in row.items()}
        per_symbol[base]["evaluated"] = len(df) - start
        print(f"{base:6s} " + "  ".join(
            f"{k[:4]}: n={len(row[k]):4d} {np.mean(row[k]) if row[k] else 0:+.3f}pp"
            for k in ("deadzone", "normal", "strong", "random")), flush=True)

    res = {k: summarise(v) for k, v in pooled.items()}
    res_out = {
        "pooled": res,
        "per_symbol": per_symbol,
        "h1_deadzone_minus_random_ci90": ci_of_difference(pooled["deadzone"], pooled["random"]),
        "h2_opened_minus_deadzone_ci90": ci_of_difference(
            pooled["normal"] + pooled["strong"], pooled["deadzone"]),
        "config": {"symbols": args.symbols, "start": args.start, "end": args.end,
                   "threshold": SPOT_THRESHOLD, "seeds": args.seeds,
                   "dead_zone": [DEAD_LO, DEAD_HI], "strong_at": STRONG},
    }

    print("\n" + "=" * 74)
    print(f"{'bucket':12s} {'n':>7s} {'mean pp':>10s} {'90% CI':>22s} {'win%':>7s} {'PF':>6s}")
    print("-" * 74)
    for k in ("deadzone", "normal", "strong", "random"):
        r = res[k]
        ci = f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]"
        print(f"{k:12s} {r['n']:7d} {r['mean']:+10.3f} {ci:>22s} {r['win_rate']:6.1f}% {r['pf']:6.2f}")
    print("=" * 74)

    opened = pooled["normal"] + pooled["strong"]
    h1 = res_out["h1_deadzone_minus_random_ci90"]
    h2 = res_out["h2_opened_minus_deadzone_ci90"]
    if h1:
        print(f"\nH1  deadzone - random  = {res['deadzone']['mean'] - res['random']['mean']:+.3f}pp"
              f"   CI [{h1[0]:+.3f}, {h1[1]:+.3f}]")
        print("    the gate costs money only if this is POSITIVE and the interval clears zero")
    if h2 and opened:
        print(f"H2  opened - deadzone  = {np.mean(opened) - res['deadzone']['mean']:+.3f}pp"
              f"   CI [{h2[0]:+.3f}, {h2[1]:+.3f}]")
        print("    the 1.2x bar separates anything only if this is POSITIVE and clears zero")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res_out, indent=1, default=str))
        print(f"\nwritten -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
