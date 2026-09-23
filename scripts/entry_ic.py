#!/usr/bin/env python3
"""Judge ENTRY rules against a matched random baseline, on data this repo has never seen.

`condition_ic.py` scores individual conditions against forward returns; `exit_ic.py`
does the same for exit rules. Neither answers the question that matters most: does the
assembled entry — 22 conditions, an adaptive threshold and five veto gates — pick better
entry points than throwing a dart?

That question has never been asked here. It is the question that falsified the sibling
project's best candidate (`Nakhoda/docs/CONCLUSION-2026-09-01.md`): candidate-v2 cleared
every tuning criterion and then lost to random entry on the holdout, −0.13R against
+0.37R.

ARMS (identical candles, identical exits, identical costs — only the entry differs):
  spotsignal    the engine as configured
  no_antichase  the same engine with no_chase + anti_fomo + entry_wick ablated,
                i.e. the same system allowed to buy strength instead of only weakness
  donchian      enter when a daily 20/10 Donchian channel flips to "in" — the rule that
                passed the sibling project's locked holdout
  random        matched entry count per symbol, drawn from the same eligible rows,
                averaged over several seeds

WHAT THIS MEASURES: the quality of an entry POINT, not of a portfolio. Every signal is
simulated independently, so a position never blocks a later one. That is deliberate —
CLAUDE.md records that `open_until` makes sequence-based comparisons in this repo
unreadable, because a lower bar fires an earlier signal whose position swallows the
window the higher bar's trade would have used. Independent entries remove that confound.

DATA: the sibling project's OKX 4h cache (20 symbols, ~8 years, hash-verified). This
repo has never touched it, so all of it is out of sample HERE. Nakhoda tuned on the
`tuning` symbols, so a Donchian figure on those is in-sample FOR DONCHIAN and is
reported as such. Nakhoda's locked holdout symbols are not read by this script at all —
that budget belongs to that project.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import MAX_HOLD_CANDLES, RESOLVED, _simulate_forward  # noqa: E402
from signals.engine import generate_signals  # noqa: E402
from signals.htf import htf_indicator_series  # noqa: E402
from backtest import _htf_at  # noqa: E402
from scripts.exit_ic import _signal_at  # noqa: E402
from signals.indicators import (  # noqa: E402
    calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_macd,
    calculate_obv, calculate_rsi, calculate_stoch_rsi, calculate_vwap,
    compute_cmf, compute_mfi,
)

logger = logging.getLogger(__name__)

NAKHODA_CACHE = Path("/Users/dfroxs/Playground/Python/Nakhoda/data/ohlcv")
TUNING = ["BTC", "ETH", "SOL", "BNB", "OKB", "ICP", "SUSHI", "NEAR", "UNI", "DOGE"]
SPLIT_DATE = "2025-08-30"          # Nakhoda's tuning/holdout_time boundary
WARMUP = 250                       # EMA200 on the base frame, plus slack
VWAP_PERIOD = 6                    # 6 x 4h = 24h, what signals/spot.py uses
TF_DELTA = {"1d": pd.Timedelta(days=1), "1w": pd.Timedelta(weeks=1)}

ANTICHASE = ("no_chase", "anti_fomo", "entry_wick")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_symbol(base: str, cache: Path = NAKHODA_CACHE, end: str | None = SPLIT_DATE):
    """One symbol's 4h frame with every column `generate_signals` reads.

    Mirrors `signals.ohlcv.fetch_ohlcv_df` exactly — the engine must not be able to
    tell this frame from a live one, or the experiment measures the loader.
    """
    path = cache / f"{base}_USDT_4h.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df["ts"], unit="ms")
    df.index.name = "timestamp"
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if end is not None:
        df = df[df.index < pd.Timestamp(end)]

    df["EMA_200"] = calculate_ema(df["close"], 200)
    df["RSI_14"] = calculate_rsi(df["close"])
    df["MACD"], df["MACD_Signal"], df["MACD_Histogram"] = calculate_macd(df["close"])
    df["BB_Upper"], df["BB_Middle"], df["BB_Lower"] = calculate_bollinger_bands(df["close"])
    df["ATR_14"] = calculate_atr(df)
    df["OBV"] = calculate_obv(df)
    df["StochRSI_K"], df["StochRSI_D"] = calculate_stoch_rsi(df["close"])
    df["VWAP_24"] = calculate_vwap(df, period=VWAP_PERIOD)
    df["MFI_14"] = compute_mfi(df)
    df["CMF_20"] = compute_cmf(df)
    return df


def eval_start(df: pd.DataFrame, frames, start: str | None, htf_mode: str) -> int:
    """First index to EVALUATE: the later of the HTF warmup and an explicit --start.

    Everything before it is still loaded, because the indicators need the history; it is
    simply not scored. That is what makes a time holdout possible without re-warming.
    """
    i = htf_ready_index(df, frames, htf_mode)
    if start is not None:
        i = max(i, int(np.searchsorted(df.index.values, np.datetime64(pd.Timestamp(start)),
                                       side="left")))
    return i


def htf_ready_index(df: pd.DataFrame, frames, htf_mode: str = "strict") -> int:
    """First 4h index at which BOTH higher timeframes have a real EMA200.

    `htf_indicator_series` labels a bar BULLISH/BEARISH by `close > ema200`, and a NaN
    EMA200 compares False — so before the 200th higher-timeframe bar every trend reads
    BEARISH. Two things then go wrong at once: the counter-trend block rejects every BUY
    on the daily, and `aligned` can never be true, which silently zeroes the HTF scoring
    condition worth up to +2.0. Live never sees this (it fetches 250 bars of each), so
    evaluating those candles would measure a warmup artefact, not the strategy.
    """
    required = ("1d", "1w") if htf_mode == "strict" else ("1d",)
    ready = None
    for tf, (series, _) in ((k, v) for k, v in frames.items() if k in required):
        if len(series) <= 199:
            return len(df)                      # never ready — symbol contributes nothing
        bar = series.index[199] + TF_DELTA[tf]
        ready = bar if ready is None else max(ready, bar)
    pos = int(np.searchsorted(df.index.values, np.datetime64(ready), side="left"))
    return max(pos, WARMUP)


def build_htf(df: pd.DataFrame):
    """1D + 1W indicator frames for `_htf_at`, resampled from the 4h base.

    `backtest._load_htf_series` refuses to resample and fetches the real higher
    timeframes, because 90 days of 4h yields ~18 weekly bars and an EMA200 needs far
    more. That objection is about the WINDOW, not the method: 8 years of 4h is ~3,100
    daily and ~450 weekly bars, so both EMAs are real here. Resampling also keeps the
    experiment offline and reproducible from one hash-verified cache.
    """
    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    frames = {}
    for tf, rule in (("1d", "1D"), ("1w", "1W")):
        d = df.resample(rule).agg(ohlc).dropna()
        series = htf_indicator_series(d)
        frames[tf] = (series, (series.index + TF_DELTA[tf]).values)
    return frames


# ---------------------------------------------------------------------------
# Entry rules
# ---------------------------------------------------------------------------

def entries_engine(df, htf_frames, start, gates_disabled=None, threshold=None,
                   only=None):
    """Every candle from `start` the engine calls a BUY, with the signal it produced.

    `only` restricts evaluation to an iterable of indices. The veto gates can only turn
    a BUY into a HOLD — never the reverse — so the ungated arm is a strict superset of
    the gated one, and the gated arm can be recovered by re-running the engine on just
    the ungated BUYs instead of every candle again. Exact, and roughly ten times
    cheaper than a second full pass.
    """
    from config import SPOT_THRESHOLD
    thr = SPOT_THRESHOLD if threshold is None else threshold
    out = []
    idx = df.index
    candidates = range(start, len(df)) if only is None else only
    for i in candidates:
        htf = _htf_at(htf_frames, idx[i]) if htf_frames else None
        sig = generate_signals(df.iloc[: i + 1], htf, None, None, mode="spot",
                               threshold_override=thr, gates_disabled=gates_disabled)
        if sig["type"] == "BUY":
            out.append((i, sig))
    return out


def entries_donchian(df, start=WARMUP, n_in=20, n_out=10):
    """Enter on the 4h candle after a DAILY 20/10 Donchian channel flips to 'in'.

    The rule is evaluated on closed daily bars and the entry is taken on the next 4h
    candle, so nothing reads a bar that had not closed.
    """
    daily = df.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    hi = daily["close"].rolling(n_in).max().shift(1)
    lo = daily["close"].rolling(n_out).min().shift(1)
    state, flips = False, []
    for ts, row in daily.iterrows():
        was = state
        if not state and pd.notna(hi[ts]) and row["close"] >= hi[ts]:
            state = True
        elif state and pd.notna(lo[ts]) and row["close"] <= lo[ts]:
            state = False
        if state and not was:
            flips.append(ts + TF_DELTA["1d"])
    pos = np.searchsorted(df.index.values, np.array(flips, dtype="datetime64[ns]"), side="left")
    return [int(p) for p in pos if start <= p < len(df)]


def split_by_gates(ungated, gated):
    """The entries the gates threw away = ungated minus gated, by candle index.

    The gates can only turn a BUY into a HOLD, so `gated` is a subset of `ungated` and
    the two halves partition it exactly. Asserted rather than assumed: if that ever stops
    holding, the `rejected` arm silently stops meaning what its name says.
    """
    kept = {i for i, _ in gated}
    stray = kept - {i for i, _ in ungated}
    if stray:
        raise ValueError(f"gated arm is not a subset of the ungated one at {sorted(stray)[:5]}")
    return [(i, sig) for i, sig in ungated if i not in kept]


def entries_random(df, n, seed, lo=WARMUP):
    hi = len(df) - 1
    if n <= 0 or hi <= lo:
        return []
    rng = random.Random(seed)
    return sorted(rng.sample(range(lo, hi), min(n, hi - lo)))


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _synthetic_signal(df, i):
    """The signal the live path would build at candle `i`, for arms with no signal dict.

    `exit_ic._signal_at` is imported rather than re-derived: two copies of the SL/TP
    geometry would drift, and a drifted baseline is worse than no baseline.
    """
    atr = df.iloc[i]["ATR_14"]
    if not atr or atr != atr or atr <= 0:
        return None
    return _signal_at(df, i)


def run_entries(df, entries, max_hold):
    """P&L per entry, all arms through the same exit simulator."""
    pnls, unresolved = [], 0
    for item in entries:
        i, sig = item if isinstance(item, tuple) else (item, _synthetic_signal(df, item))
        if sig is None:
            continue
        trade = _simulate_forward(df, i, sig, max_hold, "4h", "spot")
        if trade is None:
            continue
        if trade["outcome"] not in RESOLVED:
            unresolved += 1
            continue
        pnls.append(float(trade["pnl_pct"]))
    return pnls, unresolved


def summarise(pnls, seed=0, n_boot=1000):
    if not pnls:
        return {"n": 0, "mean": 0.0, "win_rate": 0.0, "pf": 0.0, "ci": (0.0, 0.0)}
    a = np.asarray(pnls, dtype=float)
    wins, losses = a[a > 0], a[a < 0]
    gross_loss = abs(losses.sum())
    rng = np.random.default_rng(seed)
    boot = rng.choice(a, size=(n_boot, len(a)), replace=True).mean(axis=1)
    return {
        "n": len(a),
        "mean": float(a.mean()),
        "win_rate": float((a > 0).mean() * 100),
        "pf": float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        "ci": (float(np.percentile(boot, 5)), float(np.percentile(boot, 95))),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=",".join(TUNING))
    ap.add_argument("--cache", type=Path, default=NAKHODA_CACHE)
    ap.add_argument("--end", default=SPLIT_DATE)
    ap.add_argument("--start", default=None,
                    help="evaluate only from this date; earlier candles still warm the indicators")
    ap.add_argument("--htf-warmup", choices=("strict", "daily"), default="strict",
                    help="strict: 1D AND 1W EMA200 must be real. daily: 1D only — the 1W "
                         "trend then reads BEARISH throughout, so `aligned` never fires and "
                         "the HTF condition is permanently 0 for BOTH engine arms. Valid "
                         "for an engine-vs-engine comparison, NOT for one against random.")
    ap.add_argument("--seeds", type=int, default=20, help="random-baseline seeds per symbol")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s — %(message)s")

    max_hold = MAX_HOLD_CANDLES["4h"]
    per_symbol, arms = {}, ("spotsignal", "rejected", "no_antichase", "donchian", "random")
    pooled = {a: [] for a in arms}

    for base in args.symbols.split(","):
        base = base.strip()
        if not base:
            continue
        df = load_symbol(base, args.cache, args.end)
        if len(df) < WARMUP + 50:
            logger.warning("%s: only %d candles — skipped", base, len(df))
            continue
        htf_frames = build_htf(df)
        start = eval_start(df, htf_frames, args.start, args.htf_warmup)
        if start >= len(df) - 50:
            logger.warning("%s: HTF never warms up in this span — skipped", base)
            continue

        e_open = entries_engine(df, htf_frames, start, gates_disabled=ANTICHASE)
        e_full = entries_engine(df, htf_frames, start, only=[i for i, _ in e_open])
        e_donc = entries_donchian(df, start)

        e_rej = split_by_gates(e_open, e_full)

        row = {}
        row["spotsignal"], _ = run_entries(df, e_full, max_hold)
        row["rejected"], _ = run_entries(df, e_rej, max_hold)
        row["no_antichase"], _ = run_entries(df, e_open, max_hold)
        row["donchian"], _ = run_entries(df, e_donc, max_hold)

        rnd = []
        for sd in range(args.seeds):
            p, _ = run_entries(df, entries_random(df, len(e_full), seed=sd, lo=start), max_hold)
            rnd.extend(p)
        row["random"] = rnd

        per_symbol[base] = {a: summarise(row[a]) for a in arms}
        per_symbol[base]["candles"] = len(df)
        per_symbol[base]["span"] = [str(df.index[start].date()), str(df.index[-1].date())]
        per_symbol[base]["evaluated"] = len(df) - start
        for a in arms:
            pooled[a].extend(row[a])

        s = per_symbol[base]
        print(f"{base:6s} {len(df) - start:6d}c  " + "  ".join(
            f"{a[:4]}: n={s[a]['n']:4d} mean={s[a]['mean']:+.3f}pp" for a in arms), flush=True)

    diff_ci = None
    if pooled["spotsignal"] and pooled["rejected"]:
        rng = np.random.default_rng(7)
        k = np.asarray(pooled["spotsignal"], float); r = np.asarray(pooled["rejected"], float)
        bk = rng.choice(k, size=(2000, len(k)), replace=True).mean(axis=1)
        br = rng.choice(r, size=(2000, len(r)), replace=True).mean(axis=1)
        d = bk - br
        diff_ci = [float(np.percentile(d, 5)), float(np.percentile(d, 95))]
        print(f"  90% CI on (kept - rejected): [{diff_ci[0]:+.3f}, {diff_ci[1]:+.3f}]")

    result = {
        "kept_minus_rejected_ci90": diff_ci,
        "pooled": {a: summarise(pooled[a]) for a in arms},
        "per_symbol": per_symbol,
        "config": {"symbols": args.symbols, "end": args.end, "start": args.start,
                   "htf_warmup": args.htf_warmup, "seeds": args.seeds,
                   "max_hold": max_hold, "antichase_ablated": list(ANTICHASE)},
    }
    p = result["pooled"]
    base_mean = p["random"]["mean"]
    print("\n" + "=" * 78)
    print(f"{'arm':14s} {'n':>7s} {'mean pp':>10s} {'90% CI':>22s} {'win%':>7s} {'PF':>6s} {'vs random':>11s}")
    print("-" * 78)
    for a in arms:
        r = p[a]
        ci = f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]"
        print(f"{a:14s} {r['n']:7d} {r['mean']:+10.3f} {ci:>22s} "
              f"{r['win_rate']:6.1f}% {r['pf']:6.2f} {r['mean'] - base_mean:+11.3f}")
    print("=" * 78)

    kept, rej = p["spotsignal"], p["rejected"]
    if kept["n"] and rej["n"]:
        d = kept["mean"] - rej["mean"]
        print(f"\nGATE VERDICT  kept {kept['mean']:+.3f}pp (n={kept['n']})  vs  "
              f"rejected {rej['mean']:+.3f}pp (n={rej['n']})   kept-rejected = {d:+.3f}pp")
        print("  the gates earn their place only if this is POSITIVE — "
              "what they throw away must be worse than what they keep.")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, default=str))
        print(f"\nwritten → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
