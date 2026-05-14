#!/usr/bin/env python3
r"""Backtesting harness — replay historical OHLCV through the full signal pipeline.

Supports SPOT 4H and FUTURES 1H modes. Simulates entry gates, trailing stop,
partial TP (TP1 50% + TP2 50%), time exit, vol exit, and execution costs.

Market structure data (funding, L/S, OI, DXY, S&P 500, stablecoin, BTC.D,
Gold, VIX) is NOT available historically without paid APIs — those conditions
score as NEUTRAL.  This means backtest results are *conservative* compared to
live trading, which is safer for evaluation.

Usage:
    python3 backtest.py              # futures 1H (default)
    python3 backtest.py --mode spot  # spot 4H
"""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd

from config import (
    EXECUTION_CONFIG,
    FUTURES_CONFIG,
    RISK_CONFIG,
    SIGNAL_THRESHOLD,
    SPOT_THRESHOLD,
)
from signals.engine import generate_signals
from signals.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_obv,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_vwap,
    classify_regime,
    compute_atr_percentile,
    detect_rsi_divergence,
    detect_support_resistance,
)
from signals.ohlcv import fetch_ohlcv_df
from signals.market_data import exchange

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 90
MAX_HOLD_CANDLES = {"1h": 72, "4h": 18}  # ~72h for both
MIN_WARMUP = 200  # candles for EMA200


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_backtest(symbol="BTC/USDT", timeframe="1h", mode="futures",
                 lookback_days=LOOKBACK_DAYS):
    """Run backtest and return (trades, summary_stats) dicts."""
    limit = lookback_days * (24 if timeframe == "1h" else 6) + MIN_WARMUP
    max_hold = MAX_HOLD_CANDLES.get(timeframe, 72)
    threshold = SPOT_THRESHOLD if mode == "spot" else SIGNAL_THRESHOLD
    ec = EXECUTION_CONFIG

    logger.info("Fetching %d days of %s %s OHLCV ...", lookback_days, mode, symbol)
    df = fetch_ohlcv_df(symbol, timeframe, limit=limit)
    if len(df) < MIN_WARMUP + 10:
        logger.error("Not enough data (got %d candles).", len(df))
        return [], {}

    trades = []
    for i in range(MIN_WARMUP, len(df) - max_hold - 1):
        window = df.iloc[:i + 1].copy()
        sr = detect_support_resistance(window)

        # HTF + regime
        try:
            htf = _compute_htf_from_df(df, i, timeframe)
            adx_df = calculate_adx(window)
            regime = classify_regime(window, adx_df)
        except Exception:
            htf = None
            regime = {"threshold_bump": 0, "size_adj": 1.0}

        # Pass base threshold only; generate_signals() applies regime + session bump once internally.
        # Pre-computing them here would double-apply (engine recomputes from same window data).
        effective_threshold = threshold

        signal = generate_signals(
            window, htf=htf, market_structure=None, sr=sr,
            mode=mode, threshold_override=effective_threshold,
        )
        signal["mode"] = mode

        if signal["type"] == "HOLD":
            continue

        # ── Entry gate simulation ──
        if not _passes_entry_gates(signal, mode, window):
            continue

        # ── Forward simulation with trailing stop + partial TP ──
        result = _simulate_forward(
            df, i, signal, max_hold, timeframe, mode, ec,
        )
        if result:
            trades.append(result)

    if not trades:
        logger.warning("No signals generated in backtest period.")
        return [], {}

    stats = _compute_stats(trades, df.iloc[MIN_WARMUP]["close"])
    return trades, stats


# ---------------------------------------------------------------------------
# Entry gate simulation
# ---------------------------------------------------------------------------

def _passes_entry_gates(signal, mode, window):
    """Simulate entry gates with historical data."""
    conf = signal.get("confidence", "WEAK")
    min_conf = "NORMAL"

    # Confidence gate
    _CL = {"WEAK": 0, "NORMAL": 1, "STRONG": 2}
    if _CL.get(conf, -1) < _CL.get(min_conf, 0):
        return False

    # Fakeout gate — check 24H wick on last candle
    last = window.iloc[-1]
    hi24 = window["high"].tail(24).max() if mode == "spot" else window["high"].tail(6).max()
    lo24 = window["low"].tail(24).min() if mode == "spot" else window["low"].tail(6).min()
    if hi24 and lo24 and hi24 != lo24:
        range_24h = hi24 - lo24
        upper_wick = (hi24 - last["close"]) / range_24h
        if signal["type"] == "BUY" and upper_wick > 0.6:
            return False  # fake bullish breakout

    # Spot-only quality gates
    if mode == "spot" and signal["type"] == "BUY":
        regime = signal.get("_regime", {})
        # Gate: regime filter — no BUY in bearish trend
        if regime.get("trend_dir") == "BEARISH":
            return False
        # Gate: trend confluence — need 2/3 bullish confirmations
        ema200 = last.get("EMA_200", last.get("ema200", 0))
        vwap = last.get("VWAP_24", last.get("vwap", 0))
        confluence = 0
        if last["close"] > ema200: confluence += 1
        if regime.get("trend_dir") == "BULLISH": confluence += 1
        if last["close"] > vwap: confluence += 1
        if confluence < 2:
            return False
        # Gate: breakout chase — block if price > VWAP+ATR and not near support
        atr = last.get("ATR_14", 0)
        sr_entry = signal.get("entry_price", 0)
        if atr and vwap and sr_entry > vwap + atr:
            return False  # FOMO entry

        # Gate: psychology SL vulnerability — SL within 0.15% below a $1k round number
        sl = signal.get("stop_loss", 0)
        if sl > 0:
            step = 1000
            next_round = (int(sl // step) + 1) * step
            if (next_round - sl) / sl * 100 <= 0.15:
                return False

    return True


# ---------------------------------------------------------------------------
# Forward simulation — trailing stop + partial TP
# ---------------------------------------------------------------------------

def _simulate_forward(df, entry_idx, signal, max_hold, timeframe, mode, ec):
    """Walk forward from entry_idx, managing trailing stop and partial TP."""
    entry_raw = signal["entry_price"]
    sl_raw = signal["stop_loss"]
    tp1_raw = signal["take_profit"]
    tp2_raw = signal.get("tp2", tp1_raw)
    atr_entry = signal.get("atr", 0)
    stype = signal["type"]

    # Apply fees + slippage on entry
    fee_pct = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
    slip = ec.get("slippage_pct", 0.05)
    entry_cost = (fee_pct + slip) / 100

    if stype == "BUY":
        entry = entry_raw * (1 + entry_cost)
        sl = sl_raw * (1 - entry_cost)
        tp1 = tp1_raw * (1 - entry_cost)
        tp2 = tp2_raw * (1 - entry_cost) if tp2_raw else None
    else:
        entry = entry_raw * (1 - entry_cost)
        sl = sl_raw * (1 + entry_cost)
        tp1 = tp1_raw * (1 + entry_cost)
        tp2 = tp2_raw * (1 + entry_cost) if tp2_raw else None

    trail = sl
    base_trail_factor = FUTURES_CONFIG.get("trailing_atr_factor", 0.9) if mode == "futures" else RISK_CONFIG.get("trailing_atr_factor", 1.0)
    post_tp1_factor   = RISK_CONFIG.get("trailing_post_tp1_factor", 0.8)
    min_adv_ratio     = RISK_CONFIG.get("trailing_advance_min_ratio", 0.5)
    partial_closed = False
    partial_pnl = 0
    exit_fee_pct = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
    exit_cost = (exit_fee_pct + slip) / 100
    # Funding exit threshold for futures (proxy — no historical funding data in backtest)
    funding_exit_gain_pct = 12.0  # close futures LONG if unrealized > 12% (high funding proxy)

    for j in range(entry_idx + 1, min(entry_idx + 1 + max_hold, len(df))):
        c = df.iloc[j]
        high, low = c["high"], c["low"]
        atr_now = c.get("ATR_14", atr_entry)

        # Time exit
        age = j - entry_idx
        if mode == "spot":
            max_hours = RISK_CONFIG.get("max_position_hours_spot", 48)
        else:
            max_hours = RISK_CONFIG.get("max_position_hours", 72)
        if age * (4 if timeframe == "4h" else 1) > max_hours:
            exit_px = c["close"]
            exit_pnl = _calc_backtest_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode)
            return _make_trade(df, entry_idx, j, signal, "TIME_EXIT", entry, exit_px, exit_pnl)

        # Vol exit — matches live: only force-close when underwater, otherwise
        # let the trail tighten the stop. Without this gate, backtest closed
        # winners on vol expansion that live now lets run.
        if atr_entry > 0 and atr_now > atr_entry * RISK_CONFIG.get("vol_expansion_exit_mult", 2.0):
            if stype == "BUY":
                gross = (c["close"] - entry) / entry * 100
            else:
                gross = (entry - c["close"]) / entry * 100
            if gross <= 0:
                exit_px = c["close"]
                exit_pnl = _calc_backtest_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode)
                return _make_trade(df, entry_idx, j, signal, "VOL_EXIT", entry, exit_px, exit_pnl)

        if stype == "BUY":
            # Funding exit proxy for futures — sustained large gain signals crowded longs
            if mode == "futures" and not partial_closed:
                unrealized = (c["close"] - entry) / entry * 100
                if unrealized > funding_exit_gain_pct:
                    exit_pnl = _calc_backtest_pnl(stype, entry, c["close"], partial_closed, partial_pnl, mode)
                    return _make_trade(df, entry_idx, j, signal, "FUNDING_EXIT", entry, c["close"], exit_pnl)

            # Advance trailing stop (with minimum advance threshold)
            if atr_now > 0:
                tf = base_trail_factor * (post_tp1_factor if partial_closed else 1.0)
                new_trail = c["close"] - atr_now * tf
                min_adv = atr_now * tf * min_adv_ratio
                if new_trail > trail + min_adv:
                    trail = new_trail

            # Partial TP1 (50%)
            if not partial_closed and high >= tp1:
                partial_pnl = (tp1 - entry) / entry * 100
                trail = entry  # move to breakeven
                partial_closed = True

            # TP2 after partial
            if partial_closed and tp2 and high >= tp2:
                remaining = (tp2 - entry) / entry * 100
                exit_pnl = partial_pnl * 0.5 + remaining * 0.5
                return _make_trade(df, entry_idx, j, signal, "WIN", entry, tp2, exit_pnl)

            # Trailing stop hit
            if low <= trail:
                net_exit = trail * (1 - exit_cost)
                remaining = (net_exit - entry) / entry * 100
                exit_pnl = partial_pnl * 0.5 + remaining * 0.5 if partial_closed else remaining
                outcome = "WIN" if net_exit >= entry else "LOSS"
                return _make_trade(df, entry_idx, j, signal, outcome, entry, trail, exit_pnl)

        else:  # SELL
            # Funding exit proxy — sustained large short gain signals crowded shorts
            if mode == "futures" and not partial_closed:
                unrealized = (entry - c["close"]) / entry * 100
                if unrealized > funding_exit_gain_pct:
                    exit_pnl = _calc_backtest_pnl(stype, entry, c["close"], partial_closed, partial_pnl, mode)
                    return _make_trade(df, entry_idx, j, signal, "FUNDING_EXIT", entry, c["close"], exit_pnl)

            if atr_now > 0:
                tf = base_trail_factor * (post_tp1_factor if partial_closed else 1.0)
                new_trail = c["close"] + atr_now * tf
                min_adv = atr_now * tf * min_adv_ratio
                if new_trail < trail - min_adv:
                    trail = new_trail

            if not partial_closed and low <= tp1:
                partial_pnl = (entry - tp1) / entry * 100
                trail = entry
                partial_closed = True

            if partial_closed and tp2 and low <= tp2:
                remaining = (entry - tp2) / entry * 100
                exit_pnl = partial_pnl * 0.5 + remaining * 0.5
                return _make_trade(df, entry_idx, j, signal, "WIN", entry, tp2, exit_pnl)

            if high >= trail:
                net_exit = trail * (1 + exit_cost)
                remaining = (entry - net_exit) / entry * 100
                exit_pnl = partial_pnl * 0.5 + remaining * 0.5 if partial_closed else remaining
                outcome = "WIN" if net_exit <= entry else "LOSS"
                return _make_trade(df, entry_idx, j, signal, outcome, entry, trail, exit_pnl)

    # Ran out of candles — still open
    return _make_trade(df, entry_idx, entry_idx + max_hold, signal, "OPEN",
                        entry, df.iloc[min(entry_idx + max_hold, len(df) - 1)]["close"], 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _calc_backtest_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode="futures"):
    """Calculate blended P&L including exit fee."""
    ec = EXECUTION_CONFIG
    fee_pct = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
    exit_cost = (fee_pct + ec.get("slippage_pct", 0.05)) / 100
    if stype == "BUY":
        gross = (exit_px * (1 - exit_cost) - entry) / entry * 100
    else:
        gross = (entry - exit_px * (1 + exit_cost)) / entry * 100
    if partial_closed:
        return partial_pnl * 0.5 + gross * 0.5
    return gross


def _make_trade(df, entry_idx, exit_idx, signal, outcome, entry, exit_px, pnl_pct):
    # `timestamp` is the DataFrame INDEX (set in fetch_ohlcv_df), so
    # df.iloc[i].get("timestamp") always returned "". Use df.index[i].
    def _idx_ts(i):
        try:
            return str(df.index[i])
        except Exception:
            return ""

    return {
        "entry_time": _idx_ts(entry_idx),
        "exit_time": _idx_ts(exit_idx) if outcome != "OPEN" else "",
        "type": signal["type"],
        "entry_price": round(entry, 2),
        "exit_price": round(exit_px, 2),
        "stop_loss": signal.get("stop_loss", 0),
        "take_profit": signal.get("take_profit", 0),
        "strength": signal.get("strength", 0),
        "confidence": signal.get("confidence", ""),
        "outcome": outcome,
        "pnl_pct": round(pnl_pct, 3),
        "candles_held": exit_idx - entry_idx,
        "reasons": "; ".join(signal.get("reasons", [])[:5]),
    }


def _candle_utc_hour(df, idx, timeframe):
    """Estimate UTC hour from candle index.

    `timestamp` is the DataFrame INDEX (set by fetch_ohlcv_df), so the old
    `df.iloc[idx]["timestamp"]` raised KeyError and the except returned 0.
    """
    try:
        ts = df.index[idx]
        if hasattr(ts, 'hour'):
            return ts.hour
        return datetime.fromisoformat(str(ts)).hour if ts else 0
    except Exception:
        return 0


def _compute_htf_from_df(df, idx, timeframe):
    """Compute HTF trend + indicators from resampled data."""
    small = df.iloc[:idx + 1].copy().set_index("timestamp")
    if timeframe == "4h":
        tf_map = {"1d": "1D", "1w": "1W"}
    else:
        tf_map = {"4h": "4h", "1d": "1D"}

    result = {"aligned": False}
    for tf_key, rule in tf_map.items():
        try:
            ohlc = small["close"].resample(rule).last().dropna()
            if len(ohlc) >= 50:
                ema200 = ohlc.ewm(span=200, adjust=False).mean() if len(ohlc) >= 200 else ohlc.ewm(span=min(50, len(ohlc)), adjust=False).mean()
                trend = "BULLISH" if ohlc.iloc[-1] > ema200.iloc[-1] else "BEARISH"
            else:
                trend = "NEUTRAL"

            # Compute HTF RSI + MACD
            if len(ohlc) >= 14:
                from signals.indicators import calculate_rsi, calculate_macd
                rsi_series = calculate_rsi(ohlc, period=14)
                rsi = rsi_series.iloc[-1] if not pd.isna(rsi_series.iloc[-1]) else 50
                rsi_zone = (
                    "oversold" if rsi < 30 else
                    "low" if rsi < 45 else
                    "neutral" if rsi < 55 else
                    "elevated" if rsi < 70 else
                    "overbought"
                )
                macd_line, signal_line, _ = calculate_macd(ohlc)
                macd_dir = "BULLISH" if macd_line.iloc[-1] > signal_line.iloc[-1] else "BEARISH"
            else:
                rsi, rsi_zone, macd_dir = 50, "neutral", "NEUTRAL"

            result[tf_key] = trend
            result[f"{tf_key}_indicators"] = {
                "rsi": round(rsi, 1),
                "rsi_zone": rsi_zone,
                "macd": macd_dir,
                "vol_trend": "RISING" if len(ohlc) >= 20 and ohlc.iloc[-5:].mean() > ohlc.iloc[-20:].mean() else "FLAT",
            }
        except Exception:
            result[tf_key] = "NEUTRAL"
            result[f"{tf_key}_indicators"] = {"rsi": 50, "rsi_zone": "neutral", "macd": "NEUTRAL", "vol_trend": "FLAT"}

    tf_keys = list(tf_map.keys())
    result["aligned"] = all(result.get(k) == result.get(tf_keys[0]) and result.get(k) != "NEUTRAL" for k in tf_keys)
    return result


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _compute_stats(trades, start_price):
    """Return summary statistics dict."""
    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "TIME_EXIT", "VOL_EXIT")]
    wins = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] != "WIN"]

    total_pnl = sum(t["pnl_pct"] for t in closed)
    win_rate = len(wins) / len(closed) if closed else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    avg_candles = sum(t["candles_held"] for t in closed) / len(closed) if closed else 0

    profit_factor = (
        sum(t["pnl_pct"] for t in wins) / abs(sum(t["pnl_pct"] for t in losses))
        if losses and sum(abs(t["pnl_pct"]) for t in losses) > 0 else float('inf') if wins else 0
    )

    cum = 0; peak = 0; max_dd = 0
    for t in closed:
        cum += t["pnl_pct"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    confidence_breakdown = {}
    for t in closed:
        c = t.get("confidence", "WEAK")
        confidence_breakdown[c] = confidence_breakdown.get(c, 0) + 1

    return {
        "total_signals": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "avg_candles_held": avg_candles,
        "total_pnl_pct": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "∞",
        "max_drawdown": round(max_dd, 2),
        "confidence_breakdown": confidence_breakdown,
        "start_price": start_price,
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

    print(f"\n   Period:           {LOOKBACK_DAYS} days")
    print(f"   Start Price:      ${stats['start_price']:,.2f}")

    print(f"\n   Total Signals:    {stats['total_signals']}")
    print(f"   Closed Trades:    {stats['closed_trades']}")
    print(f"   Open (max hold):  {stats['open_trades']}")

    print(f"\n   Wins:             {stats['wins']}")
    print(f"   Losses:           {stats['losses']}")
    print(f"   Win Rate:         {stats['win_rate']:.1%}")
    print(f"   Avg Win:          {stats['avg_win_pct']:+.2f}%")
    print(f"   Avg Loss:         {stats['avg_loss_pct']:+.2f}%")
    print(f"   Avg Hold:         {stats['avg_candles_held']:.0f} candles")

    print(f"\n   Total P&L:        {stats['total_pnl_pct']:+.2f}%")
    print(f"   Profit Factor:    {stats['profit_factor']}")
    print(f"   Max Drawdown:     {stats['max_drawdown']:.2f}%")

    cb = stats.get("confidence_breakdown", {})
    if cb:
        parts = [f"{k}: {v}" for k, v in sorted(cb.items())]
        print(f"   By Confidence:    {', '.join(parts)}")

    print(f"\n   (Market structure data not available historically — conservative estimate)")

    if trades:
        print(f"\n   Recent trades:")
        for t in trades[-8:]:
            if t["outcome"] == "WIN":
                icon = "\U0001f7e2"
            elif t["outcome"] in ("LOSS",):
                icon = "\U0001f534"
            elif t["outcome"] in ("TIME_EXIT", "VOL_EXIT"):
                icon = "\U0001f7e0"
            else:
                icon = "⚪"
            conf = t.get("confidence", "")
            print(
                f"   {icon} {t['type']:5s} | ${t['entry_price']:>10,.2f} → "
                f"{t['outcome']:>10s} ({t['pnl_pct']:+.2f}%) | "
                f"{t['candles_held']:>3d}c | {t['strength']:.1f} · {conf}"
            )

    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpotSignal Backtest")
    parser.add_argument("--mode", choices=["spot", "futures"], default="futures",
                        help="Trading mode (default: futures)")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS,
                        help=f"Lookback days (default: {LOOKBACK_DAYS})")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    tf = "4h" if args.mode == "spot" else "1h"
    trades, stats = run_backtest(mode=args.mode, timeframe=tf, lookback_days=args.days)
    print_backtest_results(trades, stats)
