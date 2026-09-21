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

from backtest import MAX_HOLD_CANDLES, RESOLVED, _simulate_forward  # noqa: E402
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


class Pnls(list):
    """list[float], index-aligned with `entries` — behaves exactly like the
    plain list the interface promises (equality, indexing, len all work
    against other lists) but also carries `.unresolved`: how many of those
    entries were OPEN rows or the zero-ATR guard rather than a real exit.

    Those entries enter the sample as 0.0, indistinguishable from a genuine
    flat trade unless counted separately — see `run_rule`.
    """

    def __init__(self, iterable, unresolved=0):
        super().__init__(iterable)
        self.unresolved = unresolved


def run_rule(df, entries, mode, timeframe, rule):
    """Net P&L per entry under one exit ruleset, index-aligned with `entries`.

    A rule is {"max_hold": int|None, "exit_params": dict|None}. `max_hold` MUST
    be part of a rule, not just `max_position_hours`: _simulate_forward's loop
    stops at `max_hold + 1` candles no matter what the hour cap says, so a rule
    asking for a longer hold through exit_params alone never reaches it — the
    frame runs out first and the position becomes an OPEN row that RESOLVED
    discards. That is the same silent-drop that made TIME_EXIT unreachable.

    OPEN rows and the zero-ATR guard both enter the returned sample as 0.0,
    which is indistinguishable from a genuinely flat trade — a rule that never
    resolves would otherwise look tied with baseline instead of untested.
    Those are counted in the returned list's `.unresolved` attribute rather
    than passed through silently.
    """
    max_hold = rule.get("max_hold") or MAX_HOLD_CANDLES[timeframe]
    out = []
    unresolved = 0
    for i in entries:
        sig = _signal_at(df, i)
        if not (sig["atr"] > 0):
            out.append(0.0)
            unresolved += 1
            continue
        t = _simulate_forward(df, i, sig, max_hold, timeframe, mode,
                              exit_params=rule.get("exit_params"))
        if t["outcome"] not in RESOLVED:
            unresolved += 1
        out.append(float(t["pnl_pct"]))
    if unresolved:
        logger.warning("%d/%d entries did not resolve (OPEN row or zero-ATR "
                       "guard) and entered the sample as 0.0",
                       unresolved, len(entries))
    return Pnls(out, unresolved=unresolved)


def paired_stats(base, cand):
    """Per-entry differences. Unequal arms are a bug, not a warning.

    `win_share` counts `d > 0` over ALL pairs including exact ties, and ties
    dominate by construction — a rule differs from baseline only for entries
    whose baseline trade was still alive at the cap, and most resolve earlier
    on the trail. `n_eff` — pairs where the arms actually differ — is what
    tells "no effect" (small n_eff) apart from "worse" (large n_eff, low
    win_share), which `n` alone cannot.
    """
    if len(base) != len(cand):
        raise ValueError(f"unpaired arms: {len(base)} vs {len(cand)}")
    diffs = [c - b for b, c in zip(base, cand)]
    return {
        "n": len(diffs),
        "n_eff": sum(1 for d in diffs if d != 0),
        "mean_diff": statistics.fmean(diffs) if diffs else 0.0,
        "win_share": (sum(1 for d in diffs if d > 0) / len(diffs)) if diffs else 0.0,
        "base_mean": statistics.fmean(base) if base else 0.0,
        "cand_mean": statistics.fmean(cand) if cand else 0.0,
    }


def _tail_margin(rules, tf):
    """Candles of tail margin needed to clear the LONGEST-held rule across ALL
    rules, baseline included — not just the baseline.

    Sized to the baseline alone, a longer candidate rule's late entries would
    be cut off by the end of the frame while the baseline's completed —
    truncation landing on one arm only, which the paired comparison would
    report as a real effect instead of an artefact of framing.
    """
    longest = max(r.get("max_hold") or MAX_HOLD_CANDLES[tf] for r in rules.values())
    return longest + 2


def run_cell(symbol, year, mode, rules, stride=6):
    """One (asset, year, mode) cell. `rules` maps name -> rule dict.

    The key "baseline" must be present and is the arm every other is paired
    against.
    """
    tf = TF_FOR[mode]
    # until is the NEXT year's Jan 1: "{year}-12-31" is midnight, which silently
    # drops the final day of every cell.
    since = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
    df = fetch_ohlcv_df(symbol, tf, since=since, until=until)
    entries = synth_entries(df, stride, tail=_tail_margin(rules, tf))
    base = run_rule(df, entries, mode, tf, rules["baseline"])
    results = {}
    for name, rule in rules.items():
        if name == "baseline":
            continue
        cand = run_rule(df, entries, mode, tf, rule)
        s = paired_stats(base, cand)
        # Surfaced rather than left to pass silently as ties — see run_rule.
        s["unresolved_base"] = base.unresolved
        s["unresolved_cand"] = cand.unresolved
        results[name] = s
    return results


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

    # H3 kept (Task 5): `partial_enabled` now exists in _simulate_forward's
    # _ALLOWED set. The CANDIDATE arm is the one with the partial DISABLED —
    # taking the whole position at TP2 instead of 50% at TP1 + 50% at TP2 —
    # per docs/superpowers/specs/2026-09-21-exit-prereg.md. A pass means the
    # partial does not earn its place; a failure means the partial stands.
    # A typo in --only must fail loudly. Silently yielding an empty rule table
    # would print nothing and read as "no cells qualified".
    KNOWN = {"H1", "H2", "H3"}
    if args.only is not None and args.only not in KNOWN:
        raise ValueError(f"--only must be one of {sorted(KNOWN)}, got {args.only!r}")
    rules = {"baseline": {}}
    if args.only in (None, "H1"):
        # 72 candles, matching what futures already gets, and the hour cap moved
        # with it: 72 x 4h = 288h. Both must move or the loop bound wins.
        rules["H1"] = {"max_hold": 72, "exit_params": {"max_position_hours": 288}}
    if args.only in (None, "H2"):
        rules["H2"] = {"exit_params": {"trailing_post_tp1_factor": 1.0}}
    if args.only in (None, "H3"):
        rules["H3"] = {"exit_params": {"partial_enabled": False}}

    rows_printed = 0
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
                      f"n={s['n']:5} n_eff={s['n_eff']:5}  "
                      f"base={s['base_mean']:+.4f}pp cand={s['cand_mean']:+.4f}pp  "
                      f"mean_diff={s['mean_diff']:+.4f}pp  "
                      f"win_share={s['win_share']:.3f}  "
                      f"unresolved={s['unresolved_base']}/{s['unresolved_cand']}")
                rows_printed += 1

    if rows_printed == 0:
        # Every cell failed (unlisted symbol, rate limit, a typo that slipped
        # past --only). Printing nothing and exiting 0 reads exactly like "no
        # cells qualified" to an unattended multi-hour run — it must not.
        logger.error("no cells produced a row — every cell failed or the "
                     "symbol/year set was empty")
        sys.exit(1)


if __name__ == "__main__":
    main()
