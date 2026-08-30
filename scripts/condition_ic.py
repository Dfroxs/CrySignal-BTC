#!/usr/bin/env python3
"""Which of the scoring conditions actually predict anything?

The full system fires ~12 times a year, so it can never be validated on its own
trade count. Its *components*, though, are evaluated on every candle — thousands
of observations — and that is a sample large enough to say something.

For each condition this measures, over the whole history:

    fires    how often it contributes anything at all
    IC       rank correlation between its net contribution and the forward return
    fwd+     mean forward return on candles where it leaned bullish
    fwd-     mean forward return on candles where it leaned bearish
    edge     fwd+ minus fwd- — the directional spread it actually delivers
    t        Welch t-statistic on that spread; |t| < 2 is indistinguishable from noise

A condition with an edge near zero is not a weak signal, it is a cost: it adds a
parameter, dilutes the ones that work, and has to be maintained.

Usage:
    python3 scripts/condition_ic.py                      # futures 1H, 180 days
    python3 scripts/condition_ic.py --mode spot --days 400
    python3 scripts/condition_ic.py --horizon 12         # 12 bars forward
"""

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import MIN_WARMUP, _htf_at, _load_htf_series
from config import SIGNAL_THRESHOLD, SPOT_THRESHOLD
from signals.engine import generate_signals
from signals.indicators import detect_support_resistance
from signals.ohlcv import fetch_ohlcv_df

logger = logging.getLogger(__name__)

G, R, Y, DIM, BLD, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def collect(mode="futures", days=180, horizons=(6,), start=None, end=None,
            symbol="BTC/USDT"):
    """Run the engine over every candle and pair each condition's contribution
    with the returns that followed, at every requested horizon.

    All horizons come from one pass: the engine loop is what costs time, forward
    returns are a shift. Sweeping horizons in one run is what makes the decisive
    question cheap to ask — does a condition's sign hold as the holding period
    changes, or does it only look predictive at the one horizon it was read at?
    """
    tf = "4h" if mode == "spot" else "1h"
    per_day = 6 if tf == "4h" else 24
    vwap_period = 6 if tf == "4h" else 24
    threshold = SPOT_THRESHOLD if mode == "spot" else SIGNAL_THRESHOLD

    if start is not None:
        # Warmup candles are fetched BEFORE the window so the first scored candle
        # already has a settled EMA200 — otherwise the opening weeks of a
        # historical span are scored on half-formed indicators.
        span = pd.Timedelta(hours=4 if tf == "4h" else 1) * MIN_WARMUP
        since_ms = int((pd.Timestamp(start) - span).timestamp() * 1000)
        until_ms = int(pd.Timestamp(end).timestamp() * 1000) if end is not None else None
        logger.info("Fetching %s %s %s from %s to %s ...", symbol, mode, tf, start, end or "now")
        df = fetch_ohlcv_df(symbol, tf, vwap_period=vwap_period,
                            since=since_ms, until=until_ms)
        htf_frames = _load_htf_series(symbol, tf, days, start=start, end=end)
        first = int(df.index.searchsorted(pd.Timestamp(start)))
        loop_start = max(MIN_WARMUP, first)
    else:
        logger.info("Fetching %d days of %s %s ...", days, mode, tf)
        df = fetch_ohlcv_df(symbol, tf, limit=days * per_day + MIN_WARMUP,
                            vwap_period=vwap_period)
        htf_frames = _load_htf_series(symbol, tf, days)
        loop_start = MIN_WARMUP

    rows = []
    stop = len(df) - max(horizons)
    if stop <= loop_start:
        raise SystemExit(f"Span too short: {len(df)} candles, need > {loop_start + max(horizons)}")
    for i in range(loop_start, stop):
        window = df.iloc[:i + 1].copy()
        try:
            htf = _htf_at(htf_frames, window.index[-1])
        except Exception:
            htf = None
        signal = generate_signals(
            window, htf=htf, market_structure=None,
            sr=detect_support_resistance(window), mode=mode,
            threshold_override=threshold,
        )
        contrib = signal.get("_contributions") or {}
        now = df["close"].iloc[i]
        row = {name: buy - sell for name, (buy, sell) in contrib.items()}
        for h in horizons:
            row[f"_fwd{h}"] = (df["close"].iloc[i + h] - now) / now * 100
        rows.append(row)

    out = pd.DataFrame(rows).fillna(0.0)
    out.attrs["span"] = f"{df.index[loop_start]:%Y-%m-%d} → {df.index[stop - 1]:%Y-%m-%d}"
    return out


def _spearman(a, b):
    """Rank correlation without a scipy dependency."""
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _welch_t(x, y):
    """Two-sample t-statistic, unequal variances."""
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return 0.0
    vx, vy = np.var(x, ddof=1), np.var(y, ddof=1)
    denom = np.sqrt(vx / nx + vy / ny)
    if denom == 0:
        return 0.0
    return float((np.mean(x) - np.mean(y)) / denom)


def analyse(data, horizon):
    fwd = data[f"_fwd{horizon}"].values
    conditions = [c for c in data.columns if not c.startswith("_fwd")]
    out = []
    for name in conditions:
        v = data[name].values
        pos, neg = fwd[v > 0], fwd[v < 0]
        fires = float((v != 0).mean())
        edge = (np.mean(pos) if len(pos) else 0.0) - (np.mean(neg) if len(neg) else 0.0)
        out.append({
            "condition": name,
            "fires": fires,
            "n_pos": len(pos),
            "n_neg": len(neg),
            "ic": _spearman(v, fwd),
            "fwd_pos": float(np.mean(pos)) if len(pos) else float("nan"),
            "fwd_neg": float(np.mean(neg)) if len(neg) else float("nan"),
            "edge": float(edge),
            "t": _welch_t(pos, neg) if len(pos) and len(neg) else 0.0,
        })
    return sorted(out, key=lambda r: -abs(r["t"]))


def report(results, data, mode, days, horizon):
    fwd = data[f"_fwd{horizon}"]
    print("\n" + "=" * 78)
    print(f"  🔬 CONDITION PREDICTIVE POWER — {mode.upper()}, "
          f"{data.attrs.get('span', f'{days}d')}, {horizon}-bar forward".center(78))
    print("=" * 78)
    print(f"\n   Span              : {data.attrs.get('span', '—')}")
    print(f"   Candles evaluated : {len(data)}")
    print(f"   Baseline drift    : {fwd.mean():+.3f}% per {horizon} bars "
          f"(σ {fwd.std():.2f}%)")
    print(f"\n   {'condition':<24} {'fires':>6} {'IC':>7} {'fwd+':>8} {'fwd-':>8} "
          f"{'edge':>8} {'t':>6}")
    print(f"   {'-' * 70}")

    for r in results:
        if r["n_pos"] == 0 and r["n_neg"] == 0:
            print(f"   {r['condition']:<24} {DIM}never fired (no historical data){RST}")
            continue
        strong = abs(r["t"]) >= 2.0
        col = (G if r["edge"] > 0 else R) if strong else DIM
        fp = f"{r['fwd_pos']:+.3f}" if r["n_pos"] else "   —"
        fn = f"{r['fwd_neg']:+.3f}" if r["n_neg"] else "   —"
        print(f"   {r['condition']:<24} {r['fires']:>5.0%} {r['ic']:>+7.3f} "
              f"{fp:>8} {fn:>8} {col}{r['edge']:>+7.3f}%{RST} {r['t']:>+6.1f}")

    real = [r for r in results if abs(r["t"]) >= 2.0]
    wrong = [r for r in real if r["edge"] < 0]
    print(f"\n   {BLD}Distinguishable from noise (|t| ≥ 2): {len(real)}/"
          f"{len([r for r in results if r['n_pos'] or r['n_neg']])}{RST}")
    if wrong:
        print(f"   {R}Significant but INVERTED (edge < 0): "
              f"{', '.join(r['condition'] for r in wrong)}{RST}")
        print(f"   {DIM}These conditions are scoring the wrong direction.{RST}")
    print("\n   " + DIM + "edge = mean forward return when the condition leans bullish, minus" + RST)
    print("   " + DIM + "when it leans bearish. Near zero means it is a parameter, not a signal." + RST)
    print("   " + Y + "Caveat: consecutive candles share overlapping forward windows, so the" + RST)
    print("   " + Y + "observations are autocorrelated and these t-statistics are optimistic." + RST)
    print("   " + Y + "Treat |t| ≥ 2 as 'worth investigating', not as proof. Replicate across" + RST)
    print("   " + Y + "horizons and periods before changing a weight on this evidence." + RST)
    print("=" * 78 + "\n")


def report_sweep(data, horizons, mode, days):
    """Sign stability across holding periods.

    A condition that predicts something should keep its sign as the horizon
    moves. One that flips is reading a different phenomenon at each scale, and a
    fixed weight cannot serve both.
    """
    per = {h: {r["condition"]: r for r in analyse(data, h)} for h in horizons}
    names = [c for c in data.columns if not c.startswith("_fwd")]
    names = [n for n in names if any(per[h][n]["n_pos"] or per[h][n]["n_neg"] for h in horizons)]
    names.sort(key=lambda n: -max(abs(per[h][n]["t"]) for h in horizons))

    print("\n" + "=" * 78)
    print(f"  📐 HORIZON SWEEP — {mode.upper()}, {data.attrs.get('span', f'{days}d')} "
          f"(t per horizon)".center(78))
    print("=" * 78)
    head = "".join(f"{str(h) + 'b':>9}" for h in horizons)
    print(f"\n   {'condition':<24}{head}   sign")
    print(f"   {'-' * (24 + 9 * len(horizons) + 8)}")
    for n in names:
        ts = [per[h][n]["t"] for h in horizons]
        signs = {1 if x > 0 else (-1 if x < 0 else 0) for x in ts if abs(x) > 0.5}
        if len(signs) > 1:
            tag, col = "flips", Y
        elif signs == {-1}:
            tag, col = "negative", R
        elif signs == {1}:
            tag, col = "positive", G
        else:
            tag, col = "flat", DIM
        cells = "".join(
            f"{G if x >= 2 else (R if x <= -2 else DIM)}{x:>+9.1f}{RST}" for x in ts
        )
        print(f"   {n:<24}{cells}   {col}{tag}{RST}")
    print(f"\n   {DIM}Bold cells are |t| ≥ 2. A condition that only clears the bar at one{RST}")
    print(f"   {DIM}horizon, or flips sign across them, is not a stable signal.{RST}")
    print("=" * 78 + "\n")


def run_matrix(symbols, years, mode, horizon):
    """Score every condition across symbols x years and report only what holds.

    A condition that clears |t| >= 2 in one cell has told you about one market in
    one year. The bar here is deliberately harsher: the SIGN must hold in every
    cell. Everything measured in this project so far failed that test, which is
    why it is the test.
    """
    cells, failures = {}, []
    for sym in symbols:
        for yr in years:
            key = f"{sym.split('/')[0]} {yr}"
            try:
                data = collect(mode, 365, (horizon,), f"{yr}-01-01", f"{yr}-12-31", sym)
                cells[key] = {r["condition"]: r for r in analyse(data, horizon)}
                logger.info("%s: %d candles", key, len(data))
            except Exception as exc:
                failures.append((key, str(exc)[:70]))
                logger.warning("%s skipped: %s", key, exc)
    return cells, failures


def report_matrix(cells, failures, mode, horizon):
    if not cells:
        print("\n  No cell completed — nothing to report.\n")
        return
    keys = list(cells)
    names = sorted({n for c in cells.values() for n in c})
    names = [n for n in names
             if any(cells[k].get(n, {}).get("n_pos") or cells[k].get(n, {}).get("n_neg")
                    for k in keys)]

    print("\n" + "=" * 78)
    print(f"  🧭 CROSS-MARKET SEARCH — {mode.upper()}, {horizon}-bar forward".center(78))
    print("=" * 78)
    if failures:
        print(f"\n  {Y}skipped: {', '.join(k for k, _ in failures)}{RST}")

    head = "".join(f"{k:>12}" for k in keys)
    print(f"\n  {'condition':<24}{head}   verdict")
    print("  " + "-" * (24 + 12 * len(keys) + 12))

    survivors = []
    for n in names:
        ts = [cells[k].get(n, {}).get("t", 0.0) for k in keys]
        signs = {1 if x > 0 else (-1 if x < 0 else 0) for x in ts if abs(x) > 0.5}
        strong = sum(1 for x in ts if abs(x) >= 2.0)
        if len(signs) == 1 and strong >= 2:
            verdict, col = (f"HOLDS {'+' if 1 in signs else '−'} ({strong}/{len(ts)} strong)",
                            G if 1 in signs else R)
            survivors.append((n, 1 if 1 in signs else -1, strong, ts))
        elif len(signs) > 1:
            verdict, col = "flips", Y
        else:
            verdict, col = "flat", DIM
        row = "".join(f"{G if x >= 2 else (R if x <= -2 else DIM)}{x:>+12.1f}{RST}" for x in ts)
        print(f"  {n:<24}{row}   {col}{verdict}{RST}")

    print(f"\n  {BLD}Conditions holding their sign across every cell: {len(survivors)}/{len(names)}{RST}")
    if survivors:
        for n, s, strong, _ in survivors:
            print(f"    {G if s > 0 else R}{n}{RST} — sign {'positive' if s > 0 else 'NEGATIVE'}, "
                  f"|t| ≥ 2 in {strong} of {len(keys)} cells")
        print(f"\n  {DIM}A negative sign is not useless — it is an inverted signal. What it is"
              f" not{RST}")
        print(f"  {DIM}is proof: cells share overlapping market history, so these are not"
              f" independent.{RST}")
    else:
        print(f"    {DIM}Nothing survived. On this evidence there is no component to build on,"
              f" and{RST}")
        print(f"    {DIM}steps 2–4 would be building on noise.{RST}")
    print("=" * 78 + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["spot", "futures"], default="futures")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--horizon", type=int, default=6,
                    help="Bars of forward return for the main table (default 6)")
    ap.add_argument("--horizons", default="",
                    help="Comma-separated horizons to sweep, e.g. 3,6,12,24,48")
    ap.add_argument("--start", default=None, metavar="YYYY-MM-DD",
                    help="Explicit window start — lets one period be tested against another")
    ap.add_argument("--end", default=None, metavar="YYYY-MM-DD",
                    help="Explicit window end (default: now)")
    ap.add_argument("--symbol", default="BTC/USDT", help="Single-run symbol")
    ap.add_argument("--matrix", action="store_true",
                    help="Search symbols x years and report only what holds its sign everywhere")
    ap.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT",
                    help="Comma-separated symbols for --matrix")
    ap.add_argument("--years", default="2023,2024,2025",
                    help="Comma-separated years for --matrix")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sweep = [int(h) for h in args.horizons.split(",") if h.strip()] if args.horizons else []
    horizons = sorted(set(sweep) | {args.horizon})

    if args.matrix:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        yrs = [y.strip() for y in args.years.split(",") if y.strip()]
        cells, failures = run_matrix(syms, yrs, args.mode, args.horizon)
        report_matrix(cells, failures, args.mode, args.horizon)
        return

    data = collect(args.mode, args.days, tuple(horizons), args.start, args.end, args.symbol)
    if data.empty:
        print("No data collected.")
        return
    report(analyse(data, args.horizon), data, args.mode, args.days, args.horizon)
    if sweep:
        report_sweep(data, horizons, args.mode, args.days)


if __name__ == "__main__":
    main()
