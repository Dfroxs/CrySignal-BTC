#!/usr/bin/env python3
"""Backtesting harness — replay historical OHLCV through the signal pipeline.

Fetches 90 days of 1H BTC/USDT data, runs ``generate_signals()`` on each
candle (after EMA warm-up), and tracks whether each signal's TP or SL
is hit first within a configurable look-ahead window.

Only technical conditions are testable (no funding, L/S, DXY, S&P 500,
stablecoin, BTC.D, OI, or liquidation history available without paid APIs).
These conditions simply score as NEUTRAL.
"""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pandas as pd

from config import FUTURES_CONFIG, RISK_CONFIG
from signals.indicators import calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_macd, calculate_obv, calculate_rsi, calculate_stoch_rsi, calculate_vwap, detect_rsi_divergence, detect_support_resistance
from signals.engine import generate_signals
from signals.ohlcv import fetch_ohlcv_df
from signals.htf import _htf_indicators
from signals.market_data import exchange  # was core_analysis import (
# 
    fetch_ohlcv_df,
    detect_support_resistance,
    generate_signals,
    get_htf_trend,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 90
MAX_HOLD_CANDLES = 72  # max candles to wait for TP/SL hit (72h)
MIN_WARMUP = 200  # candles needed for EMA200
SLIPPAGE_PCT = 0.001  # 0.1% per side (entry + exit) — typical BTC/USDT market impact


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_backtest(symbol="BTC/USDT", timeframe="1h",
                 lookback_days=LOOKBACK_DAYS):
    """Run backtest and return (trades, summary_stats) dicts."""
    logger.info("Fetching %d days of %s OHLCV ...", lookback_days, symbol)
    df = fetch_ohlcv_df(symbol, timeframe, limit=lookback_days * 24 + MIN_WARMUP)
    if len(df) < MIN_WARMUP + 10:
        logger.error("Not enough data for backtest (got %d candles).", len(df))
        return [], {}

    trades = []
    for i in range(MIN_WARMUP, len(df) - MAX_HOLD_CANDLES - 1):
        window = df.iloc[:i + 1].copy()

        # Recompute indicators on the truncated window
        # (fetch_ohlcv_df always recomputes — but slicing preserves them,
        #  and since they're calculated on the full series they're accurate
        #  for the subset too. We only need the latest row.)
        sr = detect_support_resistance(window)

        # HTF trend at this point in history
        try:
            htf = _compute_htf_from_df(df, i)
        except Exception:
            htf = None

        signal = generate_signals(window, htf=htf, market_structure=None, sr=sr)

        if signal["type"] == "HOLD":
            continue

        # Simulate forward
        raw_entry = signal["entry_price"]
        raw_sl    = signal["stop_loss"]
        raw_tp    = signal["take_profit"]

        # Apply slippage: BUY pays more on entry, receives less on exit
        if signal["type"] == "BUY":
            entry = raw_entry * (1 + SLIPPAGE_PCT)
            sl    = raw_sl    * (1 - SLIPPAGE_PCT)
            tp    = raw_tp    * (1 - SLIPPAGE_PCT)
        else:
            entry = raw_entry * (1 - SLIPPAGE_PCT)
            sl    = raw_sl    * (1 + SLIPPAGE_PCT)
            tp    = raw_tp    * (1 + SLIPPAGE_PCT)

        outcome = "OPEN"
        hit_candle = None

        for j in range(i + 1, min(i + 1 + MAX_HOLD_CANDLES, len(df))):
            high = df.iloc[j]["high"]
            low  = df.iloc[j]["low"]

            if signal["type"] == "BUY":
                if high >= tp:
                    outcome, hit_candle = "WIN", j
                    break
                if low <= sl:
                    outcome, hit_candle = "LOSS", j
                    break
            else:  # SELL
                if low <= tp:
                    outcome, hit_candle = "WIN", j
                    break
                if high >= sl:
                    outcome, hit_candle = "LOSS", j
                    break

        pnl_pct = 0
        if outcome == "WIN":
            pnl_pct = abs(tp - entry) / entry * 100
            if signal["type"] == "SELL":
                pnl_pct = abs(entry - tp) / entry * 100
        elif outcome == "LOSS":
            pnl_pct = -(abs(entry - sl) / entry * 100)

        entry_time = df.iloc[i]["timestamp"]
        exit_time = df.iloc[hit_candle]["timestamp"] if hit_candle else None

        trades.append({
            "entry_time":    str(entry_time),
            "exit_time":     str(exit_time) if exit_time else "",
            "type":          signal["type"],
            "entry_price":   entry,
            "stop_loss":     sl,
            "take_profit":   tp,
            "strength":      signal["strength"],
            "outcome":       outcome,
            "pnl_pct":       round(pnl_pct, 3),
            "candles_held":  (hit_candle - i) if hit_candle else 0,
            "reasons":       "; ".join(signal["reasons"]),
        })

    if not trades:
        logger.warning("No signals generated in backtest period.")
        return [], {}

    stats = _compute_stats(trades, df.iloc[MIN_WARMUP]["close"])
    return trades, stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_htf_from_df(df, idx):
    """Compute HTF trend from the 4H and 1D subsets of *df* at *idx*."""
    # Resample to 4H and 1D from the 1H data up to idx
    small = df.iloc[:idx + 1].copy().set_index("timestamp")
    result = {"4h": "NEUTRAL", "1d": "NEUTRAL", "aligned": False}

    for tf_key, rule in [("4h", "4h"), ("1d", "1D")]:
        try:
            ohlc = small["close"].resample(rule).last().dropna()
            if len(ohlc) >= 200:
                ema200 = ohlc.ewm(span=200, adjust=False).mean()
                result[tf_key] = (
                    "BULLISH" if ohlc.iloc[-1] > ema200.iloc[-1] else "BEARISH"
                )
        except Exception:
            pass

    result["aligned"] = result["4h"] == result["1d"] and result["4h"] != "NEUTRAL"
    return result


def _compute_stats(trades, start_price):
    """Return summary statistics dict from a list of trade dicts."""
    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS")]
    wins = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] == "LOSS"]

    total_pnl = sum(t["pnl_pct"] for t in closed)
    win_rate = len(wins) / len(closed) if closed else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0

    profit_factor = (
        sum(t["pnl_pct"] for t in wins) / abs(sum(t["pnl_pct"] for t in losses))
        if losses and sum(abs(t["pnl_pct"]) for t in losses) > 0 else 0
    )

    # Drawdown (cumulative P&L peak-to-trough)
    cum = 0
    peak = 0
    max_dd = 0
    for t in closed:
        cum += t["pnl_pct"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "total_signals":  len(trades),
        "closed_trades":  len(closed),
        "open_trades":    len(trades) - len(closed),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       win_rate,
        "avg_win_pct":    avg_win,
        "avg_loss_pct":   avg_loss,
        "total_pnl_pct":  round(total_pnl, 2),
        "profit_factor":  round(profit_factor, 2),
        "max_drawdown":   round(max_dd, 2),
        "start_price":    start_price,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_backtest_results(trades, stats):
    """Pretty-print backtest results."""
    if not stats:
        print("\n⚠️  No backtest results to display.\n")
        return

    print("\n" + "=" * 70)
    print("  📊 BACKTEST RESULTS".center(70))
    print("=" * 70)

    print(f"\n   Period:           {LOOKBACK_DAYS} days (1H candles)")
    print(f"   Start Price:      ${stats['start_price']:,.2f}")

    print(f"\n   Total Signals:    {stats['total_signals']}")
    print(f"   Closed Trades:    {stats['closed_trades']}")
    print(f"   Open (72h max):   {stats['open_trades']}")

    print(f"\n   Wins:             {stats['wins']}")
    print(f"   Losses:           {stats['losses']}")
    print(f"   Win Rate:         {stats['win_rate']:.1%}")
    print(f"   Avg Win:          {stats['avg_win_pct']:+.2f}%")
    print(f"   Avg Loss:         {stats['avg_loss_pct']:+.2f}%")

    print(f"\n   Total P&L:        {stats['total_pnl_pct']:+.2f}%")
    print(f"   Profit Factor:    {stats['profit_factor']:.2f}")
    print(f"   Max Drawdown:     {stats['max_drawdown']:.2f}%")

    print(f"\n   (Technical conditions only — market structure data not available historically)")

    # Recent trades
    if trades:
        print(f"\n   Recent trades:")
        for t in trades[-8:]:
            icon = "\U0001f7e2" if t["outcome"] == "WIN" else (
                "\U0001f534" if t["outcome"] == "LOSS" else "\u26aa")
            print(
                f"   {icon} {t['type']:5s} | {t['entry_price']:>10,.2f} → "
                f"{t['outcome']:5s} ({t['pnl_pct']:+.2f}%) | "
                f"held {t['candles_held']}h | strength {t['strength']:.2f}"
            )

    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    trades, stats = run_backtest()
    print_backtest_results(trades, stats)
