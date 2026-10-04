#!/usr/bin/env python3
"""PAPER_RUN.md item #5 — does the backtest reproduce the live bot's own scoring?

The live bot logs `buy_score` for every cycle. Spot skips the futures-only conditions
(funding, L/S, OI, basis are 0.00 in CONDITION_MAX), but it does NOT skip everything with
no historical source: `market_structure` scores DXY, S&P500, stablecoin supply and BTC
dominance at 0.75 each, and `gold_vix` adds 0.50. That is **3.50 of SPOT_MAX_SCORE 22.50**
the live bot can see and no replay can — 15.6%, not the 2% an earlier reading of this
claimed.

The detection floor is therefore 3.50 points, and that is the honest limit of this
comparison: a drift smaller than that is indistinguishable from the market-structure
scoring the replay is blind to.

WHAT THIS FOUND, and why the comparison is shaped the way it is:

`signals/ohlcv.fetch_ohlcv_df` returns the exchange's CURRENT, UNCLOSED candle as the
last row, and `engine.generate_signals` scores `df.iloc[-1]`. The live bot evaluates spot
at :01 past the hour, so the bar it scores is ONE MINUTE OLD — high, low and close are all
within a few dollars of the open. `backtest.py` scores closed bars. The two paths are
therefore never looking at the same bar, and live's input cannot be reconstructed
afterwards: the exchange only serves that candle's final OHLC, not its state at minute one.

So this does not pretend to replay live exactly. It measures the gap between live's logged
score and the score the SAME engine produces on CLOSED bars at the same moment — which is
what backtest.py would have computed. That difference is the drift.

Run:  ./venv/bin/python scripts/live_vs_backtest.py --db data/backups/vps-<...>.db
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import _htf_at  # noqa: E402
from signals.engine import generate_signals  # noqa: E402
from signals.htf import htf_indicator_series  # noqa: E402
from signals.ohlcv import fetch_ohlcv_df  # noqa: E402

TF_DELTA = {"1d": pd.Timedelta(days=1), "1w": pd.Timedelta(weeks=1)}
BLIND_MAX = 3.50             # market_structure 3.00 + gold_vix 0.50


def live_rows(db: Path, mode: str = "spot"):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT timestamp, price, buy_score, sell_score, threshold, type "
        "FROM cycle_log WHERE mode=? ORDER BY timestamp", (mode,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def htf_frames(symbol="BTC/USDT"):
    """The same 1D + 1W series the live path reads, per-bar."""
    out = {}
    for tf in ("1d", "1w"):
        d = fetch_ohlcv_df(symbol, tf, limit=400, vwap_period=6)
        series = htf_indicator_series(d)
        out[tf] = (series, (series.index + TF_DELTA[tf]).values)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0, help="only the newest N cycles")
    args = ap.parse_args()

    rows = live_rows(args.db)
    if args.limit:
        rows = rows[-args.limit:]
    df = fetch_ohlcv_df("BTC/USDT", "4h", limit=1000, vwap_period=6)
    frames = htf_frames()

    recs, skipped = [], 0
    for r in rows:
        t = pd.Timestamp(r["timestamp"])
        closed = df[df.index + pd.Timedelta(hours=4) <= t]      # bars fully closed by t
        if len(closed) < 250:
            skipped += 1
            continue
        sig = generate_signals(closed, _htf_at(frames, t), None, None, mode="spot",
                               threshold_override=r["threshold"])
        recs.append({
            "ts": r["timestamp"],
            "live_price": r["price"],
            "bt_close": float(closed.iloc[-1]["close"]),
            "live_buy": r["buy_score"],
            "bt_buy": sig["buy_score"],
            "diff": sig["buy_score"] - r["buy_score"],
            "live_type": r["type"],
            "bt_type": sig["type"],
        })

    if not recs:
        print("no comparable cycles — is the OHLCV window long enough?")
        return 1

    d = [x["diff"] for x in recs]
    within = sum(abs(v) <= BLIND_MAX for v in d)
    agree = sum(x["live_type"] == x["bt_type"] for x in recs)
    print(f"compared {len(recs)} spot cycles ({skipped} skipped for short history)\n")
    print(f"{'buy_score diff':22s} mean {statistics.fmean(d):+.3f}  median "
          f"{statistics.median(d):+.3f}  min {min(d):+.3f}  max {max(d):+.3f}")
    print(f"{'within blind spot 3.50':22s} {within}/{len(recs)} = {within/len(recs)*100:.0f}%")
    disagree = [x for x in recs if x["live_type"] != x["bt_type"]]
    print(f"{'verdict DISAGREES':22s} {len(disagree)}/{len(recs)} = {len(disagree)/len(recs)*100:.0f}%")
    print(f"{'verdict agrees':22s} {agree}/{len(recs)} = {agree/len(recs)*100:.0f}%")

    worst = sorted(recs, key=lambda x: -abs(x["diff"]))[:8]
    print(f"\n{'timestamp':20s} {'live px':>9s} {'bt close':>9s} {'live':>6s} {'bt':>6s} "
          f"{'diff':>7s}  {'live/bt verdict'}")
    for x in worst:
        print(f"{x['ts']:20s} {x['live_price']:9.0f} {x['bt_close']:9.0f} "
              f"{x['live_buy']:6.2f} {x['bt_buy']:6.2f} {x['diff']:+7.2f}  "
              f"{x['live_type']}/{x['bt_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
