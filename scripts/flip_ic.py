#!/usr/bin/env python3
"""Score H-FLP exactly as pre-registered: `docs/superpowers/specs/2026-11-16-futures-flip-prereg.md`.

Does closing-and-flipping help futures? One candidate: **flip off**. While a futures
position is open an opposite signal is skipped, as a same-direction one already is, and the
position runs to its own exits. The baseline is the live rule: the opposite signal closes
the position at that bar's close (`_simulate_forward(..., exit_params={"flip_at": i})`,
priced by the same `_net_pnl`), and the new position opens only if the flip gates allow.

The engine scores every bar once per window (`backtest._score_candle`, stateless), and the
same signal stream is replayed under both rules, so the only difference is the rule.

  C  BTC, primary 2022-01-01 -> 2025-10-01 (contaminated: guard), confirmation 2025-10-01 -> 2026-10-09
  D  ETH BNB XRP LINK, 2022-01-01 -> 2026-10-09 (fresh to flip tests)

The unit of comparison is the calendar month (the two rules open different trades, so
trade pairing is impossible): `diff = candidate - baseline`, monthly sum of closed-trade P&L.

    ./venv/bin/python scripts/flip_ic.py --smoke                       # counts only, no P&L
    ./venv/bin/python scripts/flip_ic.py --fidelity --db data/snap_futures.db --start "2026-10-17 14:39:46"
    ./venv/bin/python scripts/flip_ic.py --db <db> --start <ts> --out docs/superpowers/specs/2026-11-16-futures-flip-run/result.json
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import pickle
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import (  # noqa: E402
    LIVE_BASE_BARS, LOOKBACK_DAYS, MAX_HOLD_CANDLES, MIN_WARMUP, RESOLVED, SIGNAL_THRESHOLD,
    _exit_signal, _failing_gates, _live_indicators, _load_htf_series, _score_candle,
    _simulate_forward, _vwap_period,
)
from scripts.live_ic import norm_ts, ts_sql  # noqa: E402
from scripts.tp1_futures_ic import geometry_ok  # noqa: E402
from scripts.trail_futures_ic import code_hash  # noqa: E402
from scripts.variant_books import fidelity, signals_for_book, split_live, utc_naive  # noqa: E402
from signals.ohlcv import fetch_ohlcv_df  # noqa: E402

TF, MODE = "1h", "futures"
HOLD = MAX_HOLD_CANDLES[TF]
COOLDOWN_N = 5                              # backtest.run_backtest's same-side cooldown, 1h
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "flip"
RETRIES = 5
N_BOOT = 5000
SEED = 2026
WIN_ARM_C = {"primary": ("2022-01-01", "2025-10-01"), "confirmation": ("2025-10-01", "2026-10-09")}
ARM_D = ["ETH/USDT", "BNB/USDT", "XRP/USDT", "LINK/USDT"]
WIN_ARM_D = ("2022-01-01", "2026-10-09")
MIN_ASSETS_AHEAD = 3                        # prereg, Criteria: of the 4 Arm D assets
MIN_FLIPS_D = 30                            # prereg, Discard: Arm D inert below this
MIN_FID_MATCH = 7                           # prereg, Discard: of the 10 run-3 live positions
ANNOTATABLE = ("MACRO_CLOSE", "BREAKER_CLOSE")
CLOSED = RESOLVED + ("FLIP",)
CAND_FLIP_OFF = True                        # the one candidate: flip off (asserted in a test)


# ── Recording: every non-HOLD signal of a window, scored once ───────────────────────────

def _window_at(df, i):
    return _live_indicators(df.iloc[max(0, i - LIVE_BASE_BARS + 1): i + 1], _vwap_period(TF))


def _record(symbol, start, end):
    """(df, [(bar index, raw signal)]) for every non-HOLD bar of [start, end)."""
    warmup = max(MIN_WARMUP, LIVE_BASE_BARS)
    since = int((pd.Timestamp(start) - pd.Timedelta(hours=1) * warmup).timestamp() * 1000)
    until = int(pd.Timestamp(end).timestamp() * 1000)
    df = fetch_ohlcv_df(symbol, TF, since=since, until=until, vwap_period=_vwap_period(TF))
    if df is None or len(df) < warmup + 10:
        raise RuntimeError(f"not enough data for {symbol} {start}..{end}")
    try:
        htf = _load_htf_series(symbol, TF, LOOKBACK_DAYS, start=start, end=end)
    except Exception as exc:  # noqa: BLE001 - as run_backtest: degraded, not dead
        logging.warning("HTF unavailable for %s (%s)", symbol, exc)
        htf = {}
    loop_start = max(LIVE_BASE_BARS - 1, int(df.index.searchsorted(pd.Timestamp(start))))
    out = []
    for i in range(loop_start, len(df) - HOLD - 1):
        _, sig = _score_candle(df, i, TF, MODE, htf, SIGNAL_THRESHOLD, None)
        if sig["type"] != "HOLD":
            sig["mode"] = MODE
            out.append((i, sig))
    return df, out, loop_start


def record_stream(symbol, start, end):
    """Cached on disk, keyed by symbol, window and a hash of the code the signals depend on
    (the Arm C cache's `code_hash`). Windows ending in the future are never cached."""
    cacheable = pd.Timestamp(end) <= pd.Timestamp.now().normalize()
    f = CACHE_DIR / f"{symbol.replace('/', '')}_{start}_{end}_{code_hash()}.pkl"
    if cacheable and f.exists():
        try:
            with f.open("rb") as fh:
                return pickle.load(fh)
        except Exception as exc:  # noqa: BLE001 - a damaged cache file is re-recorded
            logging.warning("flip cache %s unreadable (%s); re-recording", f.name, exc)
    rec = None
    for attempt in range(RETRIES):
        try:
            rec = _record(symbol, start, end)
            break
        except Exception as exc:  # noqa: BLE001 - a window is retried, never dropped
            logging.warning("record %s %s..%s attempt %d: %s", symbol, start, end, attempt + 1, exc)
            time.sleep(10 * (attempt + 1))
    if rec is None:
        raise RuntimeError(f"no data for {symbol} {start}..{end} after {RETRIES} tries")
    if cacheable:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(rec, fh)
        tmp.replace(f)
    return rec


# ── The two rules ─────────────────────────────────────────────────────────────────────

def flip_unprofitable(open_type, open_entry, new_sig):
    """run_bot's flip gate: the closed position's raw price move (no costs, no TP1 blend)
    is worse than the new signal's expected reward from the flip price."""
    px = new_sig["entry_price"]
    if open_type == "SELL":
        pnl = (open_entry - px) / open_entry * 100
    else:
        pnl = (px - open_entry) / open_entry * 100
    reward = abs(new_sig.get("take_profit", px) - px) / px * 100 if px > 0 else 0
    return pnl < -reward


def run_sequence(df, stream, flip, cooldown_n=COOLDOWN_N, window_fn=None):
    """Replay `stream` [(i, raw signal)] as one futures book, one position at a time.

    flip=True  is the live rule: an opposite signal that meets an open position closes it
               at that bar's close (outcome FLIP) unless the position already exited on
               or before that bar; the new position then opens if the first-entry gates,
               less the re-entry check live does not apply to a flip, and
               `flip_unprofitable` allow. No cooldown follows a FLIP.
    flip=False is the candidate: the opposite signal is skipped like a same-side one.

    Other exits: same-side cooldown and the re-entry anchor, exactly as run_backtest.
    """
    window_fn = window_fn or (lambda i: _window_at(df, i))
    trades, flips, opened_i, unresolved = [], [], [], 0
    pos = None
    cooldown_until = {"BUY": -1, "SELL": -1}
    last_resolved = {}

    def settle(p):
        nonlocal unresolved
        t = p["nat"]
        if t["outcome"] not in CLOSED:
            unresolved += 1
            return
        trades.append({"i0": p["i0"], "exit_i": p["exit_i"], "type": p["type"],
                       "pnl": float(t["pnl_pct"]), "outcome": t["outcome"]})
        cooldown_until[p["type"]] = p["exit_i"] + cooldown_n
        if t["outcome"] in ("WIN", "LOSS"):
            last_resolved[p["type"]] = (p["sig"]["entry_price"], p["sig"].get("strength", 0),
                                        df.index[min(p["exit_i"], len(df) - 1)])

    def open_pos(i, sig):
        esig = _exit_signal(copy.deepcopy(sig), MODE)
        nat = _simulate_forward(df, i, copy.deepcopy(esig), HOLD, TF, MODE)
        if not nat:
            return None
        opened_i.append(i)
        return {"i0": i, "type": sig["type"], "sig": sig, "esig": esig, "nat": nat,
                "exit_i": i + nat["candles_held"]}

    for i, sig in stream:
        if pos and i > pos["exit_i"]:
            settle(pos)
            pos = None
        if pos:
            if sig["type"] == pos["type"] or not flip or i >= pos["exit_i"]:
                continue                     # same side, flip off, or already exited this bar
            t = _simulate_forward(df, pos["i0"], copy.deepcopy(pos["esig"]), HOLD, TF, MODE,
                                  exit_params={"flip_at": i})
            if not t or t["outcome"] != "FLIP":
                continue                     # exited at or before bar i: nothing to flip
            rec = {"i": i, "type": pos["type"], "pnl": float(t["pnl_pct"]),
                   "natural_pnl": float(pos["nat"]["pnl_pct"]),
                   "natural_resolved": pos["nat"]["outcome"] in CLOSED, "opened": False}
            trades.append({"i0": pos["i0"], "exit_i": i, "type": pos["type"],
                           "pnl": rec["pnl"], "outcome": "FLIP"})
            fails = [g for g in _failing_gates(sig, MODE, window_fn(i), last_resolved)
                     if g != "reentry_first"]
            if not fails and not flip_unprofitable(pos["type"], pos["sig"]["entry_price"], sig):
                pos = open_pos(i, sig)
                rec["opened"] = pos is not None
            else:
                pos = None
            flips.append(rec)
            continue
        if i < cooldown_until[sig["type"]]:
            continue
        if _failing_gates(sig, MODE, window_fn(i), last_resolved):
            continue
        pos = open_pos(i, sig)
    if pos:
        settle(pos)
    return {"trades": trades, "flips": flips, "opened_i": opened_i, "unresolved": unresolved}


# ── Statistics ────────────────────────────────────────────────────────────────────────

def monthly_sums(df, trades, start, end):
    """Closed-trade P&L (pp) summed by the calendar month of the exit bar, over every month
    of [start, end): a month with no trade is 0."""
    months = pd.period_range(pd.Timestamp(start), pd.Timestamp(end) - pd.Timedelta(days=1), freq="M")
    out = pd.Series(0.0, index=months)
    for t in trades:
        m = df.index[min(t["exit_i"], len(df) - 1)].to_period("M")
        if m in out.index:
            out[m] += t["pnl"]
    return out


def pooled_ci(diff_matrix, seed=SEED, n_boot=N_BOOT):
    """Mean of a (months x assets) diff matrix and a 90% CI from resampling whole months,
    so the cross-asset correlation inside a month is kept."""
    m = np.asarray(diff_matrix, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(m), (n_boot, len(m)))
    boots = m[idx].mean(axis=(1, 2))
    return float(m.mean()), [float(np.percentile(boots, 5)), float(np.percentile(boots, 95))]


def verdict(d_mean, d_ci_low, assets_ahead, flips_d, c_totals):
    """Prereg Criteria. `c_totals` = candidate - baseline total for each Arm C window."""
    if d_mean <= 0:
        return "FAIL"
    if flips_d < MIN_FLIPS_D:
        return "INCONCLUSIVE"
    if d_ci_low > 0 and assets_ahead >= MIN_ASSETS_AHEAD and all(t > 0 for t in c_totals):
        return "PASS"
    return "INCONCLUSIVE"


def summarise(seq):
    tr, fl = seq["trades"], seq["flips"]
    pn = [t["pnl"] for t in tr]
    return {"trades": len(tr), "mean": float(np.mean(pn)) if pn else 0.0, "total": float(sum(pn)),
            "win": float(np.mean([p > 0 for p in pn])) if pn else 0.0,
            "flips": len(fl), "flip_mean": float(np.mean([f["pnl"] for f in fl])) if fl else 0.0,
            "flip_opened_share": float(np.mean([f["opened"] for f in fl])) if fl else 0.0,
            "flipped_own_exit_mean": float(np.mean([f["natural_pnl"] for f in fl if f["natural_resolved"]]))
            if any(f["natural_resolved"] for f in fl) else 0.0,
            "unresolved": seq["unresolved"]}


def run_window(symbol, start, end):
    df, stream, loop_start = record_stream(symbol, start, end)
    base = run_sequence(df, stream, flip=True)
    cand = run_sequence(df, stream, flip=False)
    mb, mc = monthly_sums(df, base["trades"], start, end), monthly_sums(df, cand["trades"], start, end)
    return {"signals": len(stream), "base": summarise(base), "cand": summarise(cand),
            "monthly_diff": (mc - mb).tolist(), "total_diff": float(mc.sum() - mb.sum()),
            "months": [str(m) for m in mb.index]}


# ── Fidelity against the live book (prereg, Discard) ─────────────────────────────────────

def fidelity_check(db, start):
    """The flip-aware BASELINE replay of the run-3 futures window against the live positions:
    >= MIN_FID_MATCH matched within +/-1 bar, every unmatched one annotatable as a
    macro-close or breaker (its own outcome, or the position before it)."""
    con = sqlite3.connect(db)
    q = ("SELECT timestamp, variants FROM cycle_log WHERE mode='futures' AND variants IS NOT NULL"
         f" AND {ts_sql('timestamp')} >= ? ORDER BY timestamp")
    rows = con.execute(q, [norm_ts(start)]).fetchall()
    if not rows:
        raise RuntimeError("no futures cycles with variants in the window")
    first = utc_naive(rows[0][0])
    since = int((first - pd.Timedelta(hours=1) * (LIVE_BASE_BARS + 20)).timestamp() * 1000)
    df = fetch_ohlcv_df("BTC/USDT", TF, since=since, vwap_period=_vwap_period(TF))
    stream = signals_for_book(rows, df.index, "base", TF)
    seq = run_sequence(df, stream, flip=True)
    pos = con.execute("SELECT id, opened_at, pyramid_entry, outcome FROM paper_positions WHERE mode='futures'"
                      f" AND {ts_sql('opened_at')} >= ? ORDER BY opened_at", [norm_ts(start)]).fetchall()
    live, _ = split_live([(p[1], p[2]) for p in pos])
    matched, unmatched = fidelity(seq["opened_i"], df.index, live, TF)
    prev_out = {p[1]: (pos[k - 1][3] if k else None) for k, p in enumerate(pos)}
    own_out = {p[1]: p[3] for p in pos}
    annotated, bad = [], []
    for ts in unmatched:
        why = [o for o in (own_out.get(ts), prev_out.get(ts)) if o in ANNOTATABLE]
        (annotated if why else bad).append({"opened_at": ts, "own": own_out.get(ts),
                                            "previous": prev_out.get(ts)})
    ok = matched >= MIN_FID_MATCH and not bad
    return {"live_positions": len(live), "matched": matched, "annotated": annotated,
            "unannotatable": bad, "replay_opened": len(seq["opened_i"]),
            "replay_flips": len(seq["flips"]), "ok": ok}


# ── Orchestration ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--start", default=None, help="run-3 futures window start (fidelity)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--smoke", action="store_true", help="counts only; no P&L is printed")
    ap.add_argument("--fidelity", action="store_true", help="only the fidelity check")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("backtest").setLevel(logging.ERROR)

    if not geometry_ok():
        print("DISCARDED: baseline stop distance != stop_distance_mult x engine stop")
        return 2
    print("geometry applied once: ok")

    if args.smoke:
        df, stream, _ = record_stream("BTC/USDT", "2026-09-01", "2026-10-09")
        base, cand = run_sequence(df, stream, True), run_sequence(df, stream, False)
        print(f"smoke BTC 2026-09-01..10-09: signals {len(stream)}  trades base {len(base['trades'])} "
              f"cand {len(cand['trades'])}  flips {len(base['flips'])}")
        return 0

    if not (args.db and args.start):
        print("--db and --start are required (the fidelity check runs first)")
        return 2
    fid = fidelity_check(args.db, args.start)
    print(f"fidelity: {fid['matched']}/{fid['live_positions']} live positions matched; "
          f"unmatched annotated {[a['opened_at'] for a in fid['annotated']]}; "
          f"unannotatable {[a['opened_at'] for a in fid['unannotatable']]}; "
          f"replay opened {fid['replay_opened']}, flips {fid['replay_flips']} -> "
          f"{'ok' if fid['ok'] else 'DISCARDED'}")
    if args.fidelity or not fid["ok"]:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"verdict": "DISCARDED" if not fid["ok"] else "fidelity only",
                                            "fidelity": fid}, indent=1, default=str))
        return 0 if fid["ok"] else 3

    c, d = {}, {}
    for name, (a, b) in WIN_ARM_C.items():
        c[name] = run_window("BTC/USDT", a, b)
        print(f"  C {name:12s} signals={c[name]['signals']:5d} flips={c[name]['base']['flips']:4d} "
              f"total diff={c[name]['total_diff']:+.2f}pp", flush=True)
    for sym in ARM_D:
        d[sym] = run_window(sym, *WIN_ARM_D)
        print(f"  D {sym:10s} signals={d[sym]['signals']:5d} flips={d[sym]['base']['flips']:4d} "
              f"total diff={d[sym]['total_diff']:+.2f}pp", flush=True)

    matrix = np.array([d[s]["monthly_diff"] for s in ARM_D]).T
    mean, ci = pooled_ci(matrix)
    ahead = sum(1 for s in ARM_D if d[s]["total_diff"] > 0)
    flips_d = sum(d[s]["base"]["flips"] for s in ARM_D)
    v = verdict(mean, ci[0], ahead, flips_d, [c[w]["total_diff"] for w in c])
    print(f"\nD: pooled mean monthly diff {mean:+.4f}pp CI90 [{ci[0]:+.4f}, {ci[1]:+.4f}], "
          f"ahead in {ahead}/4 assets, flips {flips_d}")
    print(f"C: " + "  ".join(f"{w} {c[w]['total_diff']:+.2f}pp" for w in c))
    print(f"\nVERDICT: {v}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"verdict": v, "fidelity": fid, "C": c, "D": d,
             "D_pooled": {"mean_monthly_diff": mean, "ci90": ci, "assets_ahead": ahead, "flips": flips_d},
             "config": {"cooldown": COOLDOWN_N, "windows_C": WIN_ARM_C, "window_D": WIN_ARM_D,
                        "n_boot": N_BOOT}}, indent=1, default=str))
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
