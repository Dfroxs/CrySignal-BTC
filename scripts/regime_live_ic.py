#!/usr/bin/env python3
"""Score H-RGL exactly as pre-registered: `docs/superpowers/specs/2026-10-10-regime-live-prereg.md`.

Are the spot BUYs the LIVE regime gate blocked (`signal_blocks`, gate `regime_bearish`)
worse than random entries in the same regime? Each blocked 4H bar is priced through the
live spot exit (`backtest._simulate_forward`) on Binance's BTCUSDT 4h mirror, against a
count-matched random arm drawn from bearish-regime bars in the same window.

Reads a PULLED copy of the run's database, never the live file:

    scp dmonk@45.151.155.178:~/playground/CrySignal-BTC/data/signal_history.db data/server.db
    ./venv/bin/python scripts/regime_live_ic.py --db data/server.db --smoke    # counts only, any day
    ./venv/bin/python scripts/regime_live_ic.py --db data/server.db \\
        --out docs/superpowers/specs/<date>-regime-live-run              # from 2026-11-11

Before SCORE_FROM it refuses to run without --smoke. --smoke prints counts (blocks,
distinct bars, replay mismatches, gate combinations) and never a price after a block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, RESOLVED, _failing_gates, _simulate_forward  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from scripts.live_ic import norm_ts, ts_sql  # noqa: E402
from signals.indicators import calculate_adx, classify_regime, detect_support_resistance  # noqa: E402

RUN3_START = "2026-10-09T12:34:20Z"           # run-3 manifest started_at
END = pd.Timestamp("2026-11-08")              # run 3 day 30, exclusive
MAX_HOLD = MAX_HOLD_CANDLES["4h"]
BAR = pd.Timedelta(hours=4)
SCORE_FROM = END + MAX_HOLD * BAR             # 2026-11-11: every entry has its full hold
MIN_N, SEEDS, N_BOOT, SEED = 10, 20, 5000, 2026
MISMATCH_MAX = 0.10
GATE_LIVE, GATE_BT = "regime_bearish", "regime_counter"


def scorable(now):
    return now >= SCORE_FROM


def load_blocks(db, start, end=END):
    """Timestamps of live spot BUYs blocked by the regime gate, inside [start, end)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT {ts_sql('timestamp')} FROM signal_blocks WHERE mode = 'spot' AND "
            f"signal_type = 'BUY' AND gate = ? AND {ts_sql('timestamp')} >= ? AND "
            f"{ts_sql('timestamp')} < ? ORDER BY 1",
            (GATE_LIVE, norm_ts(start), norm_ts(end))).fetchall()
    finally:
        con.close()
    return [pd.Timestamp(r[0]) for r in rows]


def load_kept(db, start, end=END):
    """Open times of live FIRST spot positions inside [start, end)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT {ts_sql('opened_at')} FROM paper_positions WHERE mode = 'spot' AND "
            f"COALESCE(pyramid_entry, 1) = 1 AND {ts_sql('opened_at')} >= ? AND "
            f"{ts_sql('opened_at')} < ? ORDER BY 1",
            (norm_ts(start), norm_ts(end))).fetchall()
    finally:
        con.close()
    return [pd.Timestamp(r[0]) for r in rows]


def bar_index(df, ts):
    """The newest 4H bar CLOSED at `ts` (open <= ts - 4h): the bar live scored."""
    return int(np.searchsorted(df.index.values, np.datetime64(ts - BAR), side="right")) - 1


def distinct_bars(df, stamps):
    return sorted({i for i in (bar_index(df, t) for t in stamps) if i >= 0})


def _bear(reg):
    return reg.get("regime") in ("TRENDING", "VOLATILE") and reg.get("trend_dir") == "BEARISH"


class Replay:
    """Regime and gates at bar i from bars <= i only. ADX is causal (ewm), so one pass
    over the whole frame equals a pass over each prefix."""

    def __init__(self, df):
        self.df, self.adx = df, calculate_adx(df)

    def regime(self, i):
        return classify_regime(self.df.iloc[: i + 1], self.adx.iloc[: i + 1])

    def gates(self, i):
        window = self.df.iloc[: i + 1]
        sig = dict(_signal_at(self.df, i), confidence="NORMAL", _regime=self.regime(i),
                   support_resistance=detect_support_resistance(window))
        return [g for g in _failing_gates(sig, "spot", window, None)
                if g not in ("confidence_first", "reentry_first")]


def simulate(df, bars, dedupe):
    """P&L per entry through the live spot exit. Returns (pnls, unresolved)."""
    pnls, unresolved, open_until = [], 0, -1
    for i in sorted(bars):
        if dedupe and i <= open_until:
            continue
        t = _simulate_forward(df, i, _signal_at(df, i), MAX_HOLD, "4h", "spot")
        if t is None:
            continue
        if dedupe:
            open_until = i + t["candles_held"]
        if t["outcome"] in RESOLVED:
            pnls.append(float(t["pnl_pct"]))
        else:
            unresolved += 1
    return pnls, unresolved


def random_pool(pool, blocked):
    """Bearish bars the gate did NOT block (amendment 2026-10-10). With the blocked bars
    in it, the random arm drew the tested bars themselves: the smoke run found all 3
    bearish bars so far were blocked ones."""
    b = set(blocked)
    return [i for i in pool if i not in b]


def summarise(p):
    a = np.asarray(p, float)
    if not len(a):
        return {"n": 0, "mean": None, "ci90": None}
    boot = np.random.default_rng(SEED).choice(a, (N_BOOT, len(a))).mean(1)
    return {"n": int(len(a)), "mean": float(a.mean()),
            "ci90": [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]}


def verdict(s, rnd_mean):
    """H-RGL1, per the prereg."""
    if s["n"] < MIN_N or rnd_mean is None or np.isnan(rnd_mean):
        return "INCONCLUSIVE"
    lo, hi = s["ci90"]
    if s["mean"] < rnd_mean and hi < rnd_mean:
        return "SUPPORTS"
    if s["mean"] > rnd_mean and lo > rnd_mean:
        return "CONTRADICTS"
    return "INCONCLUSIVE"


def counts(df, rp, blocks, start, end=END):
    """Everything --smoke may print. Reads no bar after any block."""
    bars = distinct_bars(df, blocks)
    mism = [str(df.index[i]) for i in bars if not _bear(rp.regime(i))]
    combos = {}
    for i in bars:
        k = "+".join(sorted(rp.gates(i))) or "(none)"
        combos[k] = combos.get(k, 0) + 1
    lo = max(bar_index(df, pd.Timestamp(norm_ts(start))), 0)   # first bar live scored in the run
    hi = bar_index(df, end)
    pool = [i for i in range(lo, min(hi, len(df) - 1) + 1) if _bear(rp.regime(i))]
    return {"blocks": len(blocks), "distinct_bars": len(bars), "mismatch": mism,
            "mismatch_share": len(mism) / len(bars) if bars else 0.0,
            "gate_combos": combos, "bear_pool": len(pool),
            "bear_pool_unblocked": len(random_pool(pool, bars)), "_bars": bars, "_pool": pool}


def score(df, rp, blocks, kept_ts, start, end=END):
    c = counts(df, rp, blocks, start, end)
    bars, pool = c.pop("_bars"), c.pop("_pool")
    res = {"counts": c}
    if c["mismatch_share"] > MISMATCH_MAX:
        res["H-RGL1"] = "DISCARDED"
        return res
    need = max(bars, default=-1) + MAX_HOLD
    if bars and need > len(df) - 1:
        raise SystemExit(f"price file ends {df.index[-1]}, needs bar {need} — not scorable yet")
    live, live_open = simulate(df, bars, dedupe=True)
    only_bars = [i for i in bars if rp.gates(i) == [GATE_BT]]
    only, _ = simulate(df, only_bars, dedupe=True)
    kept, _ = simulate(df, distinct_bars(df, kept_ts), dedupe=True)
    pool = random_pool(pool, bars)
    rnd = []
    for sd in range(SEEDS):
        pick = random.Random(SEED + sd).sample(pool, min(len(live), len(pool))) if live else []
        rnd += simulate(df, pick, dedupe=False)[0]
    s = summarise(live)
    rnd_mean = float(np.mean(rnd)) if rnd else float("nan")
    v = verdict(s, rnd_mean) if len(pool) >= len(live) else "INCONCLUSIVE"
    res.update({"regime_live": s, "regime_live_unresolved": live_open,
                "random_bear_mean": rnd_mean, "random_n": len(rnd), "random_pool": len(pool),
                "regime_only_live": summarise(only), "kept_live": summarise(kept),
                "H-RGL1": v})
    return res


def load_prices(path, end):
    from scripts.entry_ic import load_symbol
    return load_symbol("BTC", path.parent, end=None if end is None else str(end))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--start", default=RUN3_START)
    ap.add_argument("--smoke", action="store_true", help="counts only; allowed any day")
    ap.add_argument("--csv", type=Path, default=None, help="reuse a fetched BTC_USDT_4h.csv")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    now = pd.Timestamp.now("UTC").tz_localize(None)
    if not args.smoke and not scorable(now):
        print(f"H-RGL may not be scored before {SCORE_FROM} UTC. Use --smoke for counts.")
        return 2
    from scripts.hb_eval import fetch
    tmp = args.out or Path("data/regime_live")
    path = args.csv or fetch(pd.Timestamp(now, tz="UTC"), tmp)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    df = load_prices(path, None)
    rp = Replay(df)
    blocks = load_blocks(args.db, args.start)
    if args.smoke:
        c = counts(df, rp, blocks, args.start)
        c.pop("_bars"), c.pop("_pool")
        print(f"H-RGL smoke  {args.db}  from {args.start}  prices sha256 {sha[:12]}…")
        print(json.dumps(c, indent=1))
        return 0
    res = {"db": str(args.db), "start": args.start, "end": str(END), "csv": str(path),
           "sha256": sha, **score(df, rp, blocks, load_kept(args.db, args.start), args.start)}
    print(json.dumps(res, indent=1, default=str))
    print(f"\n→ H-RGL1 {res['H-RGL1']}")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "result.json").write_text(json.dumps(res, indent=1, default=str))
        print(f"written → {args.out / 'result.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
