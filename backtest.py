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
from datetime import datetime

import pandas as pd

from config import (
    EXECUTION_CONFIG,
    FUTURES_CONFIG,
    RISK_CONFIG,
    SIGNAL_THRESHOLD,
    SPOT_THRESHOLD,
)
from signals.engine import generate_signals
from signals.htf import _htf_aligned, htf_indicator_series, indicators_from_row
from signals.indicators import detect_support_resistance
from signals.ohlcv import _fetch_ohlcv_paged, fetch_ohlcv_df
from signals.market_data import get_signal_confidence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 90
MAX_HOLD_CANDLES = {"1h": 72, "4h": 18}  # ~72h for both
MIN_WARMUP = 200  # candles for EMA200

# Higher timeframes per base timeframe — must match signals/htf.py.
_HTF_FOR = {"4h": ("1d", "1w"), "1h": ("4h", "1d")}
_TF_DELTA = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1),
             "1w": pd.Timedelta(weeks=1)}
_HTF_WARMUP = 250          # bars of EMA200 warmup before the window even starts


def _htf_bars_needed(tf, lookback_days):
    """Bars of *tf* required to cover the window plus EMA200 warmup.

    A fixed limit silently ran out: 1000 bars of 4H is 166 days, so a 180-day
    backtest lost its 4H trend for the first fortnight — the same class of
    quiet degradation that made the resampled HTF useless.
    """
    per_day = {"4h": 6, "1d": 1, "1w": 1 / 7}[tf]
    return int(lookback_days * per_day) + _HTF_WARMUP


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
    # Track active positions per direction so we don't fire duplicate signals
    # while a same-side trade is still open. Live caps at 1 BUY + 1 SELL via
    # max_positions; without this, backtest over-counted by ~3× whenever HTF
    # alignment kept firing the same BUY hourly through a bull leg.
    open_until = {"BUY": -1, "SELL": -1}
    # Last RESOLVED trade per direction — feeds the re-entry quality gate that
    # live applies to first entries (run_bot._check_reentry_quality).
    last_resolved = {}
    htf_frames = _load_htf_series(symbol, timeframe, lookback_days)
    # Same-side cooldown after exit (audit #9). Stops the bot from immediately
    # re-entering the exact setup that just stopped out. 5 candles on 1H, 2 on 4H.
    cooldown_n = 5 if timeframe == "1h" else 2
    cooldown_until = {"BUY": -1, "SELL": -1}
    for i in range(MIN_WARMUP, len(df) - max_hold - 1):
        window = df.iloc[:i + 1].copy()
        sr = detect_support_resistance(window)

        try:
            htf = _htf_at(htf_frames, window.index[-1])
        except Exception:
            htf = None

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

        # Skip if same-direction trade is still open (mirrors live max_positions).
        if i <= open_until[signal["type"]]:
            continue

        # Same-side cooldown after exit.
        if i < cooldown_until[signal["type"]]:
            continue

        # ── Entry gate simulation ──
        if not _passes_entry_gates(signal, mode, window, last_resolved):
            continue

        # ── Forward simulation with trailing stop + partial TP ──
        result = _simulate_forward(
            df, i, signal, max_hold, timeframe, mode, ec,
        )
        if result:
            trades.append(result)
            exit_idx = i + result["candles_held"]
            open_until[signal["type"]] = exit_idx
            cooldown_until[signal["type"]] = exit_idx + cooldown_n
            if result["outcome"] in ("WIN", "LOSS"):
                last_resolved[signal["type"]] = (
                    signal["entry_price"], signal.get("strength", 0),
                )

    if not trades:
        logger.warning("No signals generated in backtest period.")
        return [], {}

    stats = _compute_stats(trades, df.iloc[MIN_WARMUP]["close"])
    stats["lookback_days"] = lookback_days
    # The evaluated span, not the span between the first and last trade — a
    # walk-forward split has to show the windows that produced nothing.
    stats["data_from"] = df.index[MIN_WARMUP]
    stats["data_to"] = df.index[-1]
    return trades, stats


# ---------------------------------------------------------------------------
# Entry gate simulation
# ---------------------------------------------------------------------------

def _passes_entry_gates(signal, mode, window, last_resolved=None):
    """Simulate entry gates with historical data — mirrors run_bot.py logic
    for both BUY and SELL across spot/futures so backtest reflects live.
    """
    conf = signal.get("confidence", "WEAK")
    min_conf = "NORMAL"
    stype = signal["type"]

    # Confidence gate
    _CL = {"WEAK": 0, "NORMAL": 1, "STRONG": 2}
    if _CL.get(conf, -1) < _CL.get(min_conf, 0):
        return False

    # Fakeout gate — symmetric on BUY upper-wick and SELL lower-wick
    last = window.iloc[-1]
    # 24 HOURS of candles — 6 bars on spot 4H, 24 bars on futures 1H. These were
    # swapped, so the gate measured 96h on spot and 6h on futures while live
    # (signals/spot.py, signals/futures.py) measured 24h in both.
    wick_bars = 6 if mode == "spot" else 24
    hi24 = window["high"].tail(wick_bars).max()
    lo24 = window["low"].tail(wick_bars).min()
    if hi24 and lo24 and hi24 != lo24:
        range_24h = hi24 - lo24
        upper_wick = (hi24 - last["close"]) / range_24h
        lower_wick = (last["close"] - lo24) / range_24h
        if stype == "BUY" and upper_wick > 0.6:
            return False  # fake bullish breakout
        if stype == "SELL" and lower_wick > 0.6:
            return False  # fake bearish breakdown

    # Direction-symmetric quality gates — apply for BOTH spot BUY and futures
    # SHORT (live applies these to futures first-entry too as of c5a5f04+ef5d55d).
    regime = signal.get("_regime", {})
    trend = regime.get("trend_dir")
    regime_lbl = regime.get("regime", "")
    ema200 = last.get("EMA_200", last.get("ema200", 0))
    vwap = last.get("VWAP_24", last.get("vwap", 0))
    atr = last.get("ATR_14", 0)
    entry_px = signal.get("entry_price", 0)
    close = last["close"]

    # Counter-trend regime block — symmetric BUY/SELL
    if regime_lbl in ("TRENDING", "VOLATILE"):
        if stype == "BUY" and trend == "BEARISH":
            return False
        if stype == "SELL" and trend == "BULLISH":
            return False

    # Trend confluence — 2/3 confirmations matching direction
    confluence = 0
    if stype == "BUY":
        if ema200 and close > ema200: confluence += 1
        if trend == "BULLISH": confluence += 1
        if vwap and close > vwap: confluence += 1
    else:
        if ema200 and close < ema200: confluence += 1
        if trend == "BEARISH": confluence += 1
        if vwap and close < vwap: confluence += 1
    if confluence < 2:
        return False

    # Breakout chase — applies to BUY only (spot lineage); SELL has its own
    # over-extended check via wick gate above.
    if stype == "BUY" and mode == "spot":
        if atr and vwap and entry_px > vwap + atr:
            return False  # FOMO entry

    # Psychology-SL vulnerability — symmetric direction
    sl = signal.get("stop_loss", 0)
    if sl > 0:
        step = 1000
        if stype == "BUY":
            next_round = (int(sl // step) + 1) * step
            if (next_round - sl) / sl * 100 <= 0.15:
                return False
        else:
            prev_round = int(sl // step) * step
            if (sl - prev_round) / sl * 100 <= 0.15:
                return False

    # S/R proximity — live gate 0e (spot) / sr_first (futures): entry sitting
    # within 1× ATR of the level it has to break through.
    sr = signal.get("support_resistance") or {}
    if atr:
        if stype == "BUY" and sr.get("resistance") and 0 < sr["resistance"] - entry_px <= atr:
            return False
        if stype == "SELL" and sr.get("support") and 0 < entry_px - sr["support"] <= atr:
            return False

    # Re-entry quality — live gate 0a: after a resolved WIN/LOSS in this
    # direction, require a better price, a confidence upgrade, or ≥ +0.3 strength.
    prev = (last_resolved or {}).get(stype)
    if prev:
        prev_entry, prev_strength = prev
        improved = (stype == "BUY" and entry_px <= prev_entry) or \
                   (stype == "SELL" and entry_px >= prev_entry)
        if not improved:
            thr = signal.get("_threshold", 0) or 0
            prev_conf = get_signal_confidence(prev_strength, thr) if thr > 0 else "WEAK"
            if _CL.get(conf, -1) <= _CL.get(prev_conf, -1) and \
               signal.get("strength", 0) < prev_strength + 0.3:
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

    # Costs are charged ONCE, in P&L, exactly as trading/paper.py does. The old
    # model shifted entry/SL/TP prices by the round-trip cost AND deducted an
    # exit cost again on the trailing/time/vol paths — while the TP2 path
    # deducted nothing. TP2 wins were flattered and trailed exits double-charged,
    # which is precisely the trade-off the trailing factors were tuned against.
    entry = entry_raw
    sl = sl_raw
    tp1 = tp1_raw
    tp2 = tp2_raw if tp2_raw else None

    trail = sl
    base_trail_factor = FUTURES_CONFIG.get("trailing_atr_factor", 0.9) if mode == "futures" else RISK_CONFIG.get("trailing_atr_factor", 1.0)
    post_tp1_factor   = RISK_CONFIG.get("trailing_post_tp1_factor", 0.8)
    min_adv_ratio     = RISK_CONFIG.get("trailing_advance_min_ratio", 0.5)
    partial_closed = False
    partial_pnl = 0
    # No FUNDING_EXIT in backtest — funding rate isn't available historically.
    # The old 12% "proxy" arbitrarily capped winners and biased win rate down
    # without modelling actual funding. Better to omit cleanly so backtest
    # reflects the TA-only path; the disclaimer in the module docstring
    # already notes that market structure exits don't apply in backtest.

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
            exit_pnl = _net_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode)
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
                exit_pnl = _net_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode)
                return _make_trade(df, entry_idx, j, signal, "VOL_EXIT", entry, exit_px, exit_pnl)

        if stype == "BUY":
            # Advance trailing stop (with minimum advance threshold)
            if atr_now > 0:
                tf = base_trail_factor * (post_tp1_factor if partial_closed else 1.0)
                new_trail = c["close"] - atr_now * tf
                min_adv = atr_now * tf * min_adv_ratio
                if new_trail > trail + min_adv:
                    trail = new_trail

            # Partial TP1 (50%)
            if not partial_closed and high >= tp1:
                signal["_partial_taken"] = True
                partial_pnl = (tp1 - entry) / entry * 100 - _costs(mode, 2)
                # Pull trail to entry − 0.5×ATR (was: snap to entry). At exact
                # BE, fees made every remainder a fee-tax LOSS even when TP1 hit.
                # Half-ATR cushion above the SL preserves most of the locked
                # gain while letting normal noise breathe.
                trail = max(trail, entry - 0.5 * atr_now if atr_now > 0 else entry)
                partial_closed = True

            # TP2 after partial
            if partial_closed and tp2 and high >= tp2:
                exit_pnl = _net_pnl(stype, entry, tp2, True, partial_pnl, mode)
                return _make_trade(df, entry_idx, j, signal, "WIN", entry, tp2, exit_pnl)

            # Trailing stop hit
            if low <= trail:
                exit_pnl = _net_pnl(stype, entry, trail, partial_closed, partial_pnl, mode)
                outcome = "WIN" if exit_pnl > 0 else "LOSS"
                return _make_trade(df, entry_idx, j, signal, outcome, entry, trail, exit_pnl)

        else:  # SELL
            if atr_now > 0:
                tf = base_trail_factor * (post_tp1_factor if partial_closed else 1.0)
                new_trail = c["close"] + atr_now * tf
                min_adv = atr_now * tf * min_adv_ratio
                if new_trail < trail - min_adv:
                    trail = new_trail

            if not partial_closed and low <= tp1:
                signal["_partial_taken"] = True
                partial_pnl = (entry - tp1) / entry * 100 - _costs(mode, 2)
                # Mirror of BUY path: cushion 0.5×ATR above entry instead of snapping to entry.
                trail = min(trail, entry + 0.5 * atr_now if atr_now > 0 else entry)
                partial_closed = True

            if partial_closed and tp2 and low <= tp2:
                exit_pnl = _net_pnl(stype, entry, tp2, True, partial_pnl, mode)
                return _make_trade(df, entry_idx, j, signal, "WIN", entry, tp2, exit_pnl)

            if high >= trail:
                exit_pnl = _net_pnl(stype, entry, trail, partial_closed, partial_pnl, mode)
                outcome = "WIN" if exit_pnl > 0 else "LOSS"
                return _make_trade(df, entry_idx, j, signal, outcome, entry, trail, exit_pnl)

    # Ran out of candles — still open
    return _make_trade(df, entry_idx, entry_idx + max_hold, signal, "OPEN",
                        entry, df.iloc[min(entry_idx + max_hold, len(df) - 1)]["close"], 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _costs(mode, sides):
    """Round-trip execution cost in percent — `sides` legs of fee + slippage."""
    ec = EXECUTION_CONFIG
    fee = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
    return (fee + ec.get("slippage_pct", 0.05)) * sides


def _net_pnl(stype, entry, exit_px, partial_closed, partial_pnl, mode="futures"):
    """Mirror of trading/paper.py::_calc_pnl — gross move minus fees + slippage,
    blended 50/50 with the TP1 half when one was taken."""
    if stype == "BUY":
        gross = (exit_px - entry) / entry * 100
    else:
        gross = (entry - exit_px) / entry * 100
    gross -= _costs(mode, 1 if partial_closed else 2)
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
        "partial": bool(signal.get("_partial_taken")),
        "reasons": "; ".join(signal.get("reasons", [])[:5]),
    }


def _load_htf_series(symbol, timeframe, lookback_days):
    """Fetch the SAME higher timeframes live uses and precompute per-bar indicators.

    Replaces resampling the base timeframe, which could never hold enough bars:
    90 days of 4H yields ~18 weekly bars, so the spot backtest's 1W trend was
    NEUTRAL at every index and `aligned` was permanently False — silently
    disabling condition 6, worth up to +2.0. The daily "EMA200" was an EMA50
    for the same reason.
    """
    frames = {}
    for tf in _HTF_FOR.get(timeframe, ("4h", "1d")):
        bars = _fetch_ohlcv_paged(symbol, tf, _htf_bars_needed(tf, lookback_days))
        d = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        d.index = pd.to_datetime(d["timestamp"], unit="ms")
        frames[tf] = (htf_indicator_series(d), _TF_DELTA[tf])
        logger.info("HTF %s: %d bars (%s → %s)", tf, len(d), d.index[0].date(), d.index[-1].date())
    return frames


def _htf_at(frames, ts):
    """HTF state as of *ts*, from bars that had already CLOSED by then.

    Live reads the still-forming HTF bar; replaying that here would leak the
    remainder of the bar backwards in time, so the backtest lags by at most one
    HTF bar instead — conservative in the right direction.
    """
    htf = {"aligned": False}
    keys = list(frames)
    for tf, (series, delta) in frames.items():
        closed = series[series.index + delta <= ts]
        if closed.empty:
            htf[tf] = "NEUTRAL"
            htf[f"{tf}_indicators"] = {}
            continue
        ind = indicators_from_row(closed.iloc[-1])
        htf[tf] = ind["trend"]
        htf[f"{tf}_indicators"] = ind

    fast, slow = keys[0], keys[1]
    trend_match = htf[fast] != "NEUTRAL" and htf[fast] == htf[slow]
    htf["aligned"] = trend_match and _htf_aligned(
        htf[f"{fast}_indicators"], htf[f"{slow}_indicators"], htf[slow]
    )
    return htf


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
# Out-of-sample stability
# ---------------------------------------------------------------------------

def walk_forward(trades, n_windows, start=None, end=None):
    """Split closed trades into *n_windows* sequential periods and stat each one.

    Parameters are fixed throughout — nothing is refitted between windows — so
    this answers the only question a single-period backtest cannot: do these
    numbers hold up in periods they were not chosen on? A profit factor that
    swings from 2.0 to 0.3 across windows is noise wearing a result's clothes.
    """
    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "TIME_EXIT", "VOL_EXIT")]
    stamped = sorted(
        ((pd.Timestamp(t["entry_time"]), t) for t in closed), key=lambda x: x[0]
    )
    # Windows span the EVALUATED PERIOD, not the first-to-last-trade range:
    # a stretch that produced no signal at all is itself a result and must show.
    first = pd.Timestamp(start) if start is not None else (stamped[0][0] if stamped else None)
    last  = pd.Timestamp(end)   if end   is not None else (stamped[-1][0] if stamped else None)
    if first is None or last is None or n_windows < 1:
        return []
    span = (last - first) / n_windows

    windows = []
    for w in range(n_windows):
        lo = first + span * w
        hi = first + span * (w + 1)
        bucket = [t for ts, t in stamped if (lo <= ts < hi) or (w == n_windows - 1 and ts == hi)]
        windows.append({
            "from": lo, "to": hi,
            "stats": _compute_stats(bucket, 0) if bucket else None,
            "n": len(bucket),
        })
    return windows


def cost_sensitivity(trades, mode, levels=(0.0, 0.05, 0.09, 0.15, 0.25)):
    """Re-price every trade at different per-side execution costs.

    Since the T4 fix, cost is subtracted in P&L only — it no longer shifts entry,
    stop or target prices, so which trades fire and where they exit is
    cost-independent and this re-pricing is exact rather than an approximation.
    A non-partial trade carries 2 legs of cost; a partial one carries
    0.5×2 + 0.5×1 = 1.5 (the TP1 half paid a round trip, the runner pays one exit).
    """
    ec = EXECUTION_CONFIG
    fee = ec["futures_fee_pct"] if mode == "futures" else ec["spot_fee_pct"]
    current = fee + ec.get("slippage_pct", 0.05)

    closed = [t for t in trades if t["outcome"] in ("WIN", "LOSS", "TIME_EXIT", "VOL_EXIT")]
    out = []
    for level in sorted(set(levels) | {round(current, 4)}):
        rows = []
        for t in closed:
            legs = 1.5 if t.get("partial") else 2.0
            rows.append(t["pnl_pct"] + (current - level) * legs)
        wins = [p for p in rows if p > 0]
        losses = [p for p in rows if p <= 0]
        gross_l = abs(sum(losses))
        out.append({
            "per_side": level,
            "round_trip": level * 2,
            "total_pnl": round(sum(rows), 2),
            "win_rate": len(wins) / len(rows) if rows else 0,
            "profit_factor": (sum(wins) / gross_l) if gross_l > 0 else (float("inf") if wins else 0),
            "is_current": abs(level - current) < 1e-9,
        })
    return out


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_walk_forward(windows):
    if not windows:
        print("\n   No closed trades to segment.\n")
        return
    print("\n" + "=" * 70)
    print("  🔁 WALK-FORWARD — fixed parameters, sequential windows".center(70))
    print("=" * 70)
    print(f"\n   {'window':<24} {'trades':>7} {'WR':>7} {'PF':>7} {'P&L':>9}")
    print(f"   {'-' * 56}")
    pfs, profitable = [], 0
    for w in windows:
        label = f"{w['from']:%Y-%m-%d} → {w['to']:%m-%d}"
        s = w["stats"]
        if not s:
            print(f"   {label:<24} {0:>7} {'no signal':>23}")
            continue
        pf = s["profit_factor"]
        pfs.append(float(pf) if pf != "∞" else float("inf"))
        if s["total_pnl_pct"] > 0:
            profitable += 1
        print(f"   {label:<24} {w['n']:>7} {s['win_rate']:>6.0%} "
              f"{str(pf):>7} {s['total_pnl_pct']:>8.2f}%")

    finite = [p for p in pfs if p != float("inf")]
    print(f"\n   Profitable windows : {profitable}/{len(windows)}")
    if finite:
        print(f"   Profit factor range: {min(finite):.2f} → {max(finite):.2f}")
        if max(finite) > 0 and min(finite) < 1 < max(finite):
            print(f"   {'⚠️  PF crosses 1.0 between windows — parameters are not stable'}")
    print("=" * 70 + "\n")


def print_cost_sensitivity(rows):
    if not rows:
        return
    print("\n" + "=" * 70)
    print("  💸 EXECUTION COST SENSITIVITY".center(70))
    print("=" * 70)
    print(f"\n   {'per side':>9} {'round trip':>11} {'P&L':>9} {'PF':>7} {'WR':>7}")
    print(f"   {'-' * 46}")
    for r in rows:
        pf = "∞" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
        mark = "  ← current" if r["is_current"] else ""
        print(f"   {r['per_side']:>8.2f}% {r['round_trip']:>10.2f}% "
              f"{r['total_pnl']:>8.2f}% {pf:>7} {r['win_rate']:>6.0%}{mark}")
    gross = next((r for r in rows if r["per_side"] == 0), None)
    cur   = next((r for r in rows if r["is_current"]), None)
    if gross and cur and gross["total_pnl"] != 0:
        eaten = gross["total_pnl"] - cur["total_pnl"]
        print(f"\n   Costs consume {eaten:+.2f}% of a {gross['total_pnl']:+.2f}% gross result")
    print("=" * 70 + "\n")

def print_backtest_results(trades, stats):
    """Pretty-print backtest results."""
    if not stats:
        print("\n⚠️  No backtest results to display.\n")
        return

    print("\n" + "=" * 70)
    print("  📊 BACKTEST RESULTS".center(70))
    print("=" * 70)

    print(f"\n   Period:           {stats.get('lookback_days', LOOKBACK_DAYS)} days")
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
    parser.add_argument("--walk-forward", type=int, default=0, metavar="N",
                        help="Also split the run into N sequential windows and stat each")
    parser.add_argument("--costs", action="store_true",
                        help="Also show P&L re-priced at other execution-cost levels")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    tf = "4h" if args.mode == "spot" else "1h"
    trades, stats = run_backtest(mode=args.mode, timeframe=tf, lookback_days=args.days)
    print_backtest_results(trades, stats)
    if args.walk_forward:
        print_walk_forward(walk_forward(
            trades, args.walk_forward,
            start=stats.get("data_from"), end=stats.get("data_to"),
        ))
    if args.costs:
        print_cost_sensitivity(cost_sensitivity(trades, args.mode))
