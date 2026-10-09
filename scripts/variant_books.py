#!/usr/bin/env python3
"""Paper books for every score variant the live bot logged — built offline.

`signals/variants.py` scores each cycle several ways and `cycle_log.variants` keeps all
of them, but only `base` (the real signal) ever trades. This turns each logged variant
into the book it WOULD have run: one position at a time, every Phase 3 gate via
`backtest._failing_gates`, exits via `backtest._simulate_forward`, sequenced by
`scripts.reentry_ic.simulate_sequence`. All imported, not copied, so a book cannot drift
from what the bot does.

`base` is replayed the same way as a fidelity check: its book should resemble the
positions the live bot actually opened over the same span.

Read a pulled copy of the run's database, never the live file:
    scp <host>:~/playground/CrySignal-BTC/data/signal_history.db data/server.db
    ./venv/bin/python scripts/variant_books.py --db data/server.db --mode futures
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import (  # noqa: E402
    MAX_HOLD_CANDLES, RESOLVED, _exit_signal, _failing_gates, _simulate_forward,
)
from config import RISK_CONFIG  # noqa: E402
from scripts.entry_ic import summarise  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from scripts.reentry_ic import simulate_sequence  # noqa: E402

TF = {"futures": "1h", "spot": "4h"}
BAR = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}
COOLDOWN = {"1h": 5, "4h": 2}          # backtest.run_backtest's same-side cooldown


def utc_naive(ts):
    """A naive UTC Timestamp from either form cycle_log rows carry."""
    t = pd.Timestamp(ts)
    return t if t.tzinfo is None else t.tz_convert("UTC").tz_localize(None)


def signals_for_book(rows, index, book, timeframe):
    """[(candle index, signal)] for one book from (timestamp, variants_json) rows.

    The bot runs one minute after a bar closes and scores that bar, which opened one
    bar-length before the close. A HOLD or a missing book is not a signal.
    """
    mode = "futures" if timeframe == "1h" else "spot"
    out, seen = [], set()
    for ts, raw in rows:
        if not raw:
            continue
        sig = json.loads(raw).get(book)
        if not sig or sig.get("type") in (None, "HOLD"):
            continue
        bar_open = utc_naive(ts).floor(timeframe) - BAR[timeframe]
        if bar_open not in index:
            continue
        i = int(index.get_loc(bar_open))
        if i in seen:
            continue
        seen.add(i)
        out.append((i, {**sig, "mode": mode}))
    return sorted(out, key=lambda x: x[0])


def random_book(df, n, start, timeframe, mode, seeds=20):
    """Long entries at `n` random bars per seed, through the same exit simulator."""
    pnls, hi = [], len(df) - MAX_HOLD_CANDLES[timeframe] - 1
    for sd in range(seeds):
        rng = random.Random(sd)
        for i in rng.sample(range(start, hi), min(n, max(hi - start, 0))):
            t = _simulate_forward(df, i, _signal_at(df, i), MAX_HOLD_CANDLES[timeframe],
                                  timeframe, mode)
            if t and t["outcome"] in RESOLVED:
                pnls.append(float(t["pnl_pct"]))
    return pnls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--mode", choices=("futures", "spot"), default="futures")
    ap.add_argument("--start", default=None, help="ISO date; default: first logged variant")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    tf = TF[args.mode]
    q = "SELECT timestamp, variants FROM cycle_log WHERE mode=? AND variants IS NOT NULL"
    params = [args.mode]
    if args.start:
        q += " AND timestamp >= ?"
        params.append(args.start)
    rows = sqlite3.connect(args.db).execute(q + " ORDER BY timestamp", params).fetchall()
    if not rows:
        print(f"no {args.mode} cycles with variants in {args.db}")
        return 1
    books = sorted({k for _, raw in rows for k in json.loads(raw)})

    from signals.ohlcv import fetch_ohlcv_df
    first = utc_naive(rows[0][0])
    since = int((first - BAR[tf] * 260).timestamp() * 1000)     # EMA200 warmup
    df = fetch_ohlcv_df("BTC/USDT", tf, since=since, vwap_period=24 if tf == "1h" else 6)
    start = int(df.index.searchsorted(first.floor(tf) - BAR[tf]))
    age = RISK_CONFIG.get("pyramid", {}).get("reentry_max_age_hours")

    def gate(sig, i, lr, max_age):
        return _failing_gates(sig, args.mode, df.iloc[: i + 1], lr, reentry_max_age_hours=max_age)

    def sim(i, sig):
        return _simulate_forward(df, i, _exit_signal(sig, args.mode), MAX_HOLD_CANDLES[tf], tf,
                                 args.mode)

    print(f"{args.db}  {args.mode}  {len(rows)} cycles  "
          f"{rows[0][0][:16]} → {rows[-1][0][:16]}  books: {', '.join(books)}\n")
    result = {}
    for b in books:
        sigs = signals_for_book(rows, df.index, b, tf)
        r = simulate_sequence(df.index, sigs, gate, sim, age, cooldown_n=COOLDOWN[tf])
        s = summarise(r["taken"])
        result[b] = {"signals": len(sigs), **s, "total": sum(r["taken"]),
                     "unresolved": r["unresolved"], "trades": r["trades"]}
        print(f"{b:15s} signals={len(sigs):4d}  trades={s['n']:3d}  mean={s['mean']:+.3f}pp  "
              f"total={sum(r['taken']):+.2f}pp  win={s['win_rate']:.1f}%  open={r['unresolved']}")
    n_rand = max((v["n"] for v in result.values()), default=0) or 10
    rnd = summarise(random_book(df, n_rand, start, tf, args.mode))
    result["random_long"] = rnd
    print(f"{'random_long':15s} {'':13s}trades={rnd['n']:3d}  mean={rnd['mean']:+.3f}pp  "
          f"(20 seeds × {n_rand})")
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
