#!/usr/bin/env python3
"""How much does scoring the UNCLOSED bar change the verdict?

`fetch_ohlcv_df` returns the exchange's forming candle as the last row and
`engine.generate_signals` scores `df.iloc[-1]`. Spot is evaluated at :01, so that bar is
one minute old — open, high, low and close within a few dollars. `backtest.py` scores
closed bars. The two can never be compared directly on live data, because the forming
bar's minute-one state is not served by any API after the fact.

It can be SIMULATED. For each closed bar i in a historical frame:

  live-shaped   bars 0..i-1 closed, plus bar i as a stub: o=h=l=c = bar i's OPEN
  replay-shaped bars 0..i-1 only

Both then go through the identical engine. The difference is what the forming bar does to
the decision, measured rather than argued about. The stub is an approximation — live's bar
has one minute of real movement, not zero — so this is a LOWER bound on the divergence.

Run:  ./venv/bin/python scripts/forming_bar_impact.py --symbols BTC,ETH --limit 800
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import _htf_at  # noqa: E402
from config import SPOT_THRESHOLD  # noqa: E402
from scripts.entry_ic import build_htf, eval_start, load_symbol  # noqa: E402
from signals.engine import generate_signals  # noqa: E402
from signals.indicators import (  # noqa: E402
    calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_macd,
    calculate_obv, calculate_rsi, calculate_stoch_rsi, calculate_vwap,
    compute_cmf, compute_mfi,
)


WINDOW = 1500          # bars fed to each arm; both arms get the identical treatment


def _indicators(f: pd.DataFrame) -> pd.DataFrame:
    """Every column the engine reads, computed over exactly the frame given.

    BOTH arms go through this. An earlier version compared the stub frame (recomputed)
    against load_symbol's columns (computed over the symbol's whole history) — a confound
    that would have shown up as "the forming bar changes everything" when the real
    difference was the window the indicators were computed on.
    """
    f["EMA_200"] = calculate_ema(f["close"], 200)
    f["RSI_14"] = calculate_rsi(f["close"])
    f["MACD"], f["MACD_Signal"], f["MACD_Histogram"] = calculate_macd(f["close"])
    f["BB_Upper"], f["BB_Middle"], f["BB_Lower"] = calculate_bollinger_bands(f["close"])
    f["ATR_14"] = calculate_atr(f)
    f["OBV"] = calculate_obv(f)
    f["StochRSI_K"], f["StochRSI_D"] = calculate_stoch_rsi(f["close"])
    f["VWAP_24"] = calculate_vwap(f, period=6)
    f["MFI_14"] = compute_mfi(f)
    f["CMF_20"] = compute_cmf(f)
    return f


def arms(raw: pd.DataFrame, i: int):
    """(replay-shaped, live-shaped) frames at bar i, identical but for the last row."""
    lo = max(0, i + 1 - WINDOW)
    closed = _indicators(raw.iloc[lo:i].copy())          # bars up to i-1, all closed
    f = raw.iloc[lo: i + 1].copy()
    o = float(f.iloc[-1]["open"])
    f.iloc[-1, f.columns.get_indexer(["open", "high", "low", "close"])] = [o, o, o, o]
    return closed, _indicators(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    ap.add_argument("--limit", type=int, default=800, help="candles per symbol")
    args = ap.parse_args()

    diffs, flips, total = [], 0, 0
    per_symbol = {}
    for base in args.symbols.split(","):
        base = base.strip()
        df = load_symbol(base)
        raw = df[["open", "high", "low", "close", "volume"]]
        frames = build_htf(df)
        start = max(eval_start(df, frames, None, "strict"), WINDOW, len(df) - args.limit)
        n, f_sym, d_sym = 0, 0, []
        for i in range(start, len(df)):
            htf = _htf_at(frames, df.index[i])
            f_closed, f_forming = arms(raw, i)
            closed = generate_signals(f_closed, htf, None, None, mode="spot",
                                      threshold_override=SPOT_THRESHOLD)
            forming = generate_signals(f_forming, htf, None, None, mode="spot",
                                       threshold_override=SPOT_THRESHOLD)
            d = forming["buy_score"] - closed["buy_score"]
            d_sym.append(d)
            n += 1
            if forming["type"] != closed["type"]:
                f_sym += 1
        per_symbol[base] = (n, f_sym, statistics.fmean(d_sym) if d_sym else 0.0,
                            max((abs(x) for x in d_sym), default=0.0))
        diffs.extend(d_sym); flips += f_sym; total += n
        print(f"{base:6s} {n:5d} kandil  verdict berbeda {f_sym:4d} ({f_sym/n*100:5.1f}%)  "
              f"mean diff {per_symbol[base][2]:+.3f}  max |diff| {per_symbol[base][3]:.2f}",
              flush=True)

    print("\n" + "=" * 70)
    print(f"{'total':16s} {total} kandil")
    print(f"{'verdict berbeda':16s} {flips}/{total} = {flips/total*100:.1f}%")
    print(f"{'buy_score diff':16s} mean {statistics.fmean(diffs):+.3f}  "
          f"median {statistics.median(diffs):+.3f}  "
          f"max |diff| {max(abs(x) for x in diffs):.2f}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
