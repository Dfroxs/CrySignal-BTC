#!/usr/bin/env python3
"""Offline backtest — runs the signal pipeline against local CSVs in data/.

Monkey-patches signals.ohlcv.fetch_ohlcv_df so backtest.py's run_backtest()
consumes data/btc_1h_90d.csv or data/btc_4h_90d.csv instead of hitting the
exchange. Lets us replay the same 90-day window deterministically.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from signals import ohlcv as _ohlcv_mod  # noqa: E402
from signals.indicators import (  # noqa: E402
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_obv,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_vwap,
    compute_cmf,
    compute_mfi,
)

CSV_MAP = {
    "1h": ROOT / "data" / "btc_1h_90d.csv",
    "4h": ROOT / "data" / "btc_4h_90d.csv",
}


def _load_csv(timeframe: str, vwap_period: int) -> pd.DataFrame:
    path = CSV_MAP[timeframe]
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()

    df["EMA_200"] = calculate_ema(df["close"], 200)
    df["RSI_14"] = calculate_rsi(df["close"])
    df["MACD"], df["MACD_Signal"], df["MACD_Histogram"] = calculate_macd(df["close"])
    df["BB_Upper"], df["BB_Middle"], df["BB_Lower"] = calculate_bollinger_bands(df["close"])
    df["ATR_14"] = calculate_atr(df)
    df["OBV"] = calculate_obv(df)
    df["StochRSI_K"], df["StochRSI_D"] = calculate_stoch_rsi(df["close"])
    df["VWAP_24"] = calculate_vwap(df, period=vwap_period)
    df["MFI_14"] = compute_mfi(df)
    df["CMF_20"] = compute_cmf(df)
    return df


def _patched_fetch(symbol="BTC/USDT", timeframe="1h", limit=500, vwap_period=24):
    df = _load_csv(timeframe, vwap_period)
    return df.tail(limit) if limit and len(df) > limit else df


_ohlcv_mod.fetch_ohlcv_df = _patched_fetch
import backtest as _bt  # noqa: E402

# Force the loader inside backtest.py to use the patched version
_bt.fetch_ohlcv_df = _patched_fetch


def _trade_table(trades, n=15):
    rows = []
    for t in trades[-n:]:
        rows.append(
            f"  {t['entry_time'][:16]} | {t['type']:4s} | "
            f"${t['entry_price']:>9,.0f} → ${t['exit_price']:>9,.0f} | "
            f"{t['outcome']:>9s} {t['pnl_pct']:+6.2f}% | "
            f"{t['candles_held']:>3d}c | str={t['strength']:.1f} {t.get('confidence','')[:1]}"
        )
    return "\n".join(rows)


def _outcome_split(trades):
    counts = {}
    for t in trades:
        counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
    return counts


def _by_direction(trades):
    """Win-rate / P&L broken down by BUY vs SELL."""
    out = {}
    for side in ("BUY", "SELL"):
        side_trades = [t for t in trades if t["type"] == side
                       and t["outcome"] in ("WIN", "LOSS", "TIME_EXIT", "VOL_EXIT")]
        wins = [t for t in side_trades if t["outcome"] == "WIN"]
        pnl = sum(t["pnl_pct"] for t in side_trades)
        out[side] = {
            "n": len(side_trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(side_trades) if side_trades else 0,
            "pnl": pnl,
            "avg": pnl / len(side_trades) if side_trades else 0,
        }
    return out


def _by_confidence(trades):
    out = {}
    for t in trades:
        if t["outcome"] not in ("WIN", "LOSS", "TIME_EXIT", "VOL_EXIT"):
            continue
        c = t.get("confidence", "")
        b = out.setdefault(c, {"n": 0, "wins": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] += t["pnl_pct"]
        if t["outcome"] == "WIN":
            b["wins"] += 1
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["spot", "futures", "both"], default="both")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    modes = ["futures", "spot"] if args.mode == "both" else [args.mode]
    all_results = {}

    for mode in modes:
        tf = "4h" if mode == "spot" else "1h"
        df_full = _load_csv(tf, vwap_period=24 if tf == "1h" else 6)
        first_ts, last_ts = df_full.index[0], df_full.index[-1]
        print(f"\n========== {mode.upper()} ({tf}) ==========")
        print(f"CSV:    {CSV_MAP[tf].name}")
        print(f"Range:  {first_ts} → {last_ts}  ({len(df_full)} candles)")
        print(f"Price:  ${df_full['close'].iloc[0]:,.0f} → ${df_full['close'].iloc[-1]:,.0f}  "
              f"({(df_full['close'].iloc[-1]/df_full['close'].iloc[0]-1)*100:+.1f}%)")

        trades, stats = _bt.run_backtest(
            symbol="BTC/USDT", timeframe=tf, mode=mode, lookback_days=args.days
        )

        if not stats:
            print("  no trades")
            continue

        print(f"\n  Signals fired:    {stats['total_signals']}")
        print(f"  Closed:           {stats['closed_trades']}   Open: {stats['open_trades']}")
        print(f"  Wins / Losses:    {stats['wins']} / {stats['losses']}    "
              f"Win rate: {stats['win_rate']:.1%}")
        print(f"  Avg win / loss:   {stats['avg_win_pct']:+.2f}% / {stats['avg_loss_pct']:+.2f}%")
        print(f"  Profit factor:    {stats['profit_factor']}")
        print(f"  Total P&L:        {stats['total_pnl_pct']:+.2f}%")
        print(f"  Max drawdown:     -{stats['max_drawdown']:.2f}%")
        print(f"  Avg hold:         {stats['avg_candles_held']:.0f} candles")

        outcomes = _outcome_split(trades)
        print(f"  Outcome split:    {outcomes}")

        dirs = _by_direction(trades)
        print(f"  BUY  : n={dirs['BUY']['n']:>2}  wins={dirs['BUY']['wins']:>2}  "
              f"WR={dirs['BUY']['win_rate']:.1%}  pnl={dirs['BUY']['pnl']:+6.2f}%  "
              f"avg={dirs['BUY']['avg']:+.2f}%")
        print(f"  SELL : n={dirs['SELL']['n']:>2}  wins={dirs['SELL']['wins']:>2}  "
              f"WR={dirs['SELL']['win_rate']:.1%}  pnl={dirs['SELL']['pnl']:+6.2f}%  "
              f"avg={dirs['SELL']['avg']:+.2f}%")

        confs = _by_confidence(trades)
        for c, b in sorted(confs.items()):
            wr = b["wins"] / b["n"] if b["n"] else 0
            print(f"  {c or 'WEAK':6s}: n={b['n']:>2}  WR={wr:.1%}  pnl={b['pnl']:+6.2f}%")

        print("\n  Last trades:")
        print(_trade_table(trades, n=15))

        all_results[mode] = {"trades": trades, "stats": stats,
                              "directions": dirs, "outcomes": outcomes,
                              "confidence": confs}

    out_path = ROOT / "data" / "backtest_offline_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {m: {"stats": r["stats"], "directions": r["directions"],
                 "outcomes": r["outcomes"], "confidence": r["confidence"]}
             for m, r in all_results.items()},
            f, indent=2, default=str,
        )
    print(f"\nSaved summary: {out_path}")


if __name__ == "__main__":
    main()
