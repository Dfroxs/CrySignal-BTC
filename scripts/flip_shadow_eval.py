#!/usr/bin/env python3
"""Score the flip shadow: H-FLP-C, `docs/superpowers/specs/2026-11-16-futures-flip-prereg.md`.

Each live flip event with an error-free Claude opinion (`shadow_flip_opinions`, first
opinion per position) is replayed down both branches with the real exit simulator:

  FLIP branch = the flipped position closed at the event bar's close (`flip_at`) plus the
                new position's replayed P&L (0 when the flip gates blocked it)
  HOLD branch = the held position run to its own exit

value = pnl(branch Claude chose) - pnl(the other branch). Unit = event. PASS if the mean
value > 0 and the 90% bootstrap CI (5,000 resamples) lies above 0; FAIL if the mean <= 0;
INCONCLUSIVE otherwise, and always INCONCLUSIVE below MIN_EVENTS events.

An opinion on a position that did NOT end as a FLIP (a stop took it in the same cycle) is
not a flip event and is counted separately.

    ./venv/bin/python scripts/flip_shadow_eval.py --db data/server.db --start "2026-10-17 14:39:46"
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, RESOLVED, _simulate_forward, _vwap_period  # noqa: E402
from scripts.live_ic import norm_ts, ts_sql  # noqa: E402

TF, MODE = "1h", "futures"
HOLD = MAX_HOLD_CANDLES[TF]
MIN_EVENTS = 20
N_BOOT = 5000
SEED = 2026
BAR = pd.Timedelta(hours=1)


def bar_index(df, ts):
    """Index of the bar the bot scored at `ts` (it runs one minute after a close and
    scores the bar that opened one bar-length before the close), or None."""
    t = pd.Timestamp(ts)
    t = t if t.tzinfo is None else t.tz_convert("UTC").tz_localize(None)
    open_ = t.floor("h") - BAR
    return int(df.index.get_loc(open_)) if open_ in df.index else None


def replay(df, i, pos, flip_at=None):
    """P&L of a stored position (its own entry, levels and ATR) from bar `i`, or None."""
    sig = {"type": pos["type"], "entry_price": pos["entry_price"], "stop_loss": pos["stop_loss"],
           "take_profit": pos["take_profit"], "tp2": pos.get("tp2") or pos["take_profit"],
           "atr": pos.get("atr") or 0}
    t = _simulate_forward(df, i, copy.deepcopy(sig), HOLD, TF, MODE,
                          exit_params=None if flip_at is None else {"flip_at": flip_at})
    if not t or t["outcome"] not in RESOLVED + ("FLIP",):
        return None
    return float(t["pnl_pct"])


def event_value(df, held, new, verdict, event_ts):
    """value of Claude's verdict on one event, or None when a branch cannot be replayed."""
    f, i0 = bar_index(df, event_ts), bar_index(df, held["opened_at"])
    if f is None or i0 is None or f <= i0:
        return None
    hold = replay(df, i0, held)
    flipped = replay(df, i0, held, flip_at=f)
    if hold is None or flipped is None:
        return None
    flip_total = flipped
    if new is not None:
        n_pnl = replay(df, bar_index(df, new["opened_at"]), new)
        if n_pnl is None:
            return None
        flip_total += n_pnl
    chosen, other = (flip_total, hold) if verdict == "FLIP" else (hold, flip_total)
    return chosen - other


def verdict_of(values, seed=SEED, n_boot=N_BOOT):
    """(verdict, mean, ci) per the prereg; INCONCLUSIVE below MIN_EVENTS."""
    v = np.asarray(values, float)
    if len(v) < MIN_EVENTS:
        return "INCONCLUSIVE", (float(v.mean()) if len(v) else None), None
    mean = float(v.mean())
    boots = np.random.default_rng(seed).choice(v, (n_boot, len(v))).mean(1)
    ci = [float(np.percentile(boots, 5)), float(np.percentile(boots, 95))]
    return ("PASS" if mean > 0 and ci[0] > 0 else "FAIL" if mean <= 0 else "INCONCLUSIVE"), mean, ci


def load_events(con, start):
    q = ("SELECT o.timestamp, o.position_id, o.verdict FROM shadow_flip_opinions o "
         "WHERE o.provider='anthropic' AND o.error IS NULL AND o.verdict IS NOT NULL"
         f" AND {ts_sql('o.timestamp')} >= ? ORDER BY o.timestamp")
    seen, out = set(), []
    for ts, pid, verdict in con.execute(q, [norm_ts(start)]):
        if pid not in seen:
            seen.add(pid)
            out.append((ts, pid, verdict))
    return out


def position(con, pid):
    cols = ("id", "type", "entry_price", "stop_loss", "take_profit", "tp2", "atr", "opened_at",
            "closed_at", "outcome")
    r = con.execute(f"SELECT {', '.join(cols)} FROM paper_positions WHERE id=?", [pid]).fetchone()
    return dict(zip(cols, r)) if r else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    events = load_events(con, args.start)
    print(f"{args.db}  flip opinions (first per position) = {len(events)}")
    if not events:
        print("VERDICT: INCONCLUSIVE (no events)")
        return 0
    from signals.ohlcv import fetch_ohlcv_df
    first = pd.Timestamp(norm_ts(args.start))
    df = fetch_ohlcv_df("BTC/USDT", TF, since=int((first - pd.Timedelta(days=12)).timestamp() * 1000),
                        vwap_period=_vwap_period(TF))
    values, rows, not_flip, unscored = [], [], 0, 0
    for ts, pid, verdict in events:
        held = position(con, pid)
        if not held or held["outcome"] != "FLIP":
            not_flip += 1
            continue
        nxt = con.execute("SELECT id FROM paper_positions WHERE mode='futures' AND id>? AND type<>? "
                          "ORDER BY id LIMIT 1", [pid, held["type"]]).fetchone()
        new = position(con, nxt[0]) if nxt and abs(
            (pd.Timestamp(position(con, nxt[0])["opened_at"]) - pd.Timestamp(held["closed_at"])).total_seconds()) < 300 else None
        val = event_value(df, held, new, verdict, ts)
        if val is None:
            unscored += 1
            continue
        values.append(val)
        rows.append({"position_id": pid, "verdict": verdict, "new_opened": new is not None, "value": val})
    v, mean, ci = verdict_of(values)
    print(f"scored events {len(values)}  not a flip {not_flip}  unscored {unscored}")
    print(f"mean value {mean}  CI90 {ci}  -> {v}" + ("  (below 20 events: always INCONCLUSIVE)"
                                                      if len(values) < MIN_EVENTS else ""))
    if args.out:
        args.out.write_text(json.dumps({"verdict": v, "mean": mean, "ci90": ci, "events": rows,
                                        "not_a_flip": not_flip, "unscored": unscored}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
