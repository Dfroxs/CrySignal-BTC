#!/usr/bin/env python3
"""Judge EXIT rules on synthetic entries.

The full system closes ~14 trades across two years of backtest, so no exit
hypothesis can be judged on its own trade count. `condition_ic.py` already
solved this for the entry side by evaluating components every candle; this is
the same move for the exit side.

"Does trailing to breakeven after TP1 beat a flat target?" is a property of
PRICE BEHAVIOUR, not of the entry signal. Measuring it needs many entry points,
not entries with an edge. Every Nth candle becomes a hypothetical BUY and each
candidate rule runs forward from that same point.

Comparisons are PAIRED: every rule sees the identical entry set, so a difference
cannot be confounded by which trades were taken — the confound that makes
threshold sweeps in this repository unreadable (see CLAUDE.md on `open_until`).

Synthetic entries have no edge, so absolute P&L here is meaningless. Only the
RELATIVE comparison between rules on the same price population is.
"""
import argparse
import logging
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, _simulate_forward  # noqa: E402
from config import RISK_CONFIG  # noqa: E402
from signals.ohlcv import fetch_ohlcv_df  # noqa: E402

logger = logging.getLogger(__name__)

TF_FOR = {"spot": "4h", "futures": "1h"}


def synth_entries(df, stride, warmup=200, tail=0):
    """Indices of hypothetical entries: every `stride`-th candle after warmup.

    Warmup exists because ATR_14 and the other indicator columns are NaN at the
    head of the frame; entering there would compare rules on undefined stops.

    `tail` drops entries too close to the end to complete. Without it the last
    entries are truncated by the frame rather than closed by a rule, and since
    rules differ in how long they hold, that truncation lands unevenly across
    arms — a difference the paired comparison would report as a real effect.
    """
    return list(range(warmup, max(warmup, len(df) - tail), stride))


def _signal_at(df, i):
    """Build the signal dict the live path would build at candle `i`.

    Uses the same formulas as the real entry path: SL at atr_multiplier x ATR,
    TP1 at take_profit_rr x risk, TP2 at twice the TP1 distance. No `mode`
    argument: none of these formulas differ by mode — `mode` is read by
    _simulate_forward, which run_rule passes it to directly.
    """
    row = df.iloc[i]
    atr = float(row["ATR_14"])
    entry = float(row["close"])
    risk = atr * RISK_CONFIG["atr_multiplier"]
    tp1 = entry + risk * RISK_CONFIG["take_profit_rr"]
    return {"type": "BUY", "entry_price": entry, "stop_loss": entry - risk,
            "take_profit": tp1, "tp2": entry + (tp1 - entry) * 2, "atr": atr}


def run_rule(df, entries, mode, timeframe, rule):
    """Net P&L per entry under one exit ruleset, index-aligned with `entries`.

    A rule is {"max_hold": int|None, "exit_params": dict|None}. `max_hold` MUST
    be part of a rule, not just `max_position_hours`: _simulate_forward's loop
    stops at `max_hold + 1` candles no matter what the hour cap says, so a rule
    asking for a longer hold through exit_params alone never reaches it — the
    frame runs out first and the position becomes an OPEN row that RESOLVED
    discards. That is the same silent-drop that made TIME_EXIT unreachable.
    """
    max_hold = rule.get("max_hold") or MAX_HOLD_CANDLES[timeframe]
    out = []
    for i in entries:
        sig = _signal_at(df, i)
        if not (sig["atr"] > 0):
            out.append(0.0)
            continue
        t = _simulate_forward(df, i, sig, max_hold, timeframe, mode,
                              exit_params=rule.get("exit_params"))
        out.append(float(t["pnl_pct"]))
    return out


def paired_stats(base, cand):
    """Per-entry differences. Unequal arms are a bug, not a warning."""
    if len(base) != len(cand):
        raise ValueError(f"unpaired arms: {len(base)} vs {len(cand)}")
    diffs = [c - b for b, c in zip(base, cand)]
    return {
        "n": len(diffs),
        "mean_diff": statistics.fmean(diffs) if diffs else 0.0,
        "win_share": (sum(1 for d in diffs if d > 0) / len(diffs)) if diffs else 0.0,
        "base_mean": statistics.fmean(base) if base else 0.0,
        "cand_mean": statistics.fmean(cand) if cand else 0.0,
    }


def run_cell(symbol, year, mode, rules, stride=6):
    """One (asset, year, mode) cell. `rules` maps name -> exit_params dict.

    The key "baseline" must be present and is the arm every other is paired
    against.
    """
    tf = TF_FOR[mode]
    # until is the NEXT year's Jan 1: "{year}-12-31" is midnight, which silently
    # drops the final day of every cell.
    since = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
    df = fetch_ohlcv_df(symbol, tf, since=since, until=until)
    # The tail margin must clear the LONGEST-held rule, not the baseline. Sized
    # to the baseline, a longer rule's late entries would be cut off by the
    # frame while the baseline's completed — truncation landing on one arm only,
    # which the paired comparison would report as a real effect.
    longest = max([r.get("max_hold") or MAX_HOLD_CANDLES[tf] for r in rules.values()])
    entries = synth_entries(df, stride, tail=longest + 2)
    base = run_rule(df, entries, mode, tf, rules["baseline"])
    return {name: paired_stats(base, run_rule(df, entries, mode, tf, rule))
            for name, rule in rules.items() if name != "baseline"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["spot", "futures"], default="futures")
    ap.add_argument("--symbols", default="BTC/USDT,ETH/USDT,BNB/USDT,XRP/USDT,LINK/USDT")
    ap.add_argument("--years", default="2020,2021,2022,2023")
    ap.add_argument("--stride", type=int, default=6,
                    help="candles between synthetic entries (default 6)")
    ap.add_argument("--only", default=None, metavar="HYPOTHESIS",
                    help="judge ONE pre-registered hypothesis. Reading a single "
                         "committed row is what removes the multiple-comparison "
                         "problem.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # H3 needs the partial disabled, which no knob does yet. Task 5 either adds
    # `partial_enabled` and registers H3 here, or drops H3. It is deliberately
    # absent rather than aliased to H2 — a rule table where two names share one
    # params dict silently reports the same number twice.
    # A typo in --only must fail loudly. Silently yielding an empty rule table
    # would print nothing and read as "no cells qualified".
    KNOWN = {"H1", "H2"}
    if args.only is not None and args.only not in KNOWN:
        raise ValueError(f"--only must be one of {sorted(KNOWN)}, got {args.only!r}")
    rules = {"baseline": {}}
    if args.only in (None, "H1"):
        # 72 candles, matching what futures already gets, and the hour cap moved
        # with it: 72 x 4h = 288h. Both must move or the loop bound wins.
        rules["H1"] = {"max_hold": 72, "exit_params": {"max_position_hours": 288}}
    if args.only in (None, "H2"):
        rules["H2"] = {"exit_params": {"trailing_post_tp1_factor": 1.0}}

    for year in [int(y) for y in args.years.split(",")]:
        for symbol in args.symbols.split(","):
            try:
                res = run_cell(symbol, year, args.mode, rules, args.stride)
            except Exception as exc:
                logger.warning("cell %s %s %s failed: %s — skipped",
                               symbol, year, args.mode, exc)
                continue
            for name, s in res.items():
                print(f"{args.mode:8} {symbol:10} {year}  {name:4} "
                      f"n={s['n']:5}  mean_diff={s['mean_diff']:+.4f}pp  "
                      f"win_share={s['win_share']:.3f}")


if __name__ == "__main__":
    main()
