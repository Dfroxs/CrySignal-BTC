#!/usr/bin/env python3
"""SpotSignal — BTC/USDT trading signal bot.

Four-phase pipeline: scrape news → analyze (spot + futures) → paper trade → notify.
"""

import argparse
import atexit
import logging
import sys
import time
from datetime import UTC, datetime

from signals.spot import analyze_spot_signal
from signals.futures import analyze_futures_signal
from signals.terminal import display_combined
from news_scraper import scrape_and_export
from notifier import _format_close_notification, _format_open_notification, _send_telegram_message, send_signal_alert
from trading.paper import check_and_close_positions, print_open_status, print_paper_summary
from config import RISK_CONFIG, FUTURES_CONFIG, RISK_LIMITS
from signals.sizing import calculate_position_size, get_pyramid_size_factor
from trading.history import close as close_db, close_paper_position, get_open_position_count_by_direction, get_open_positions, has_open_position_same_direction, log_signal_block, open_paper_position

_file_handler   = logging.FileHandler("spotsignal.log")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.WARNING)   # INFO stays in file only
_console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s — %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])
logger = logging.getLogger(__name__)


# ── Terminal progress helpers ────────────────────────────────────────────────

_W  = "\033[0m"   # reset
_B  = "\033[1m"   # bold
_G  = "\033[92m"  # green
_Y  = "\033[93m"  # yellow
_R  = "\033[91m"  # red
_DIM = "\033[2m"  # dim

def _step(icon, text, color=None, end="\n"):
    c = color or _W
    print(f"  {c}{icon}{_W}  {text}", end=end, flush=True)

def _loading(text):
    print(f"  {_DIM}⟳{_W}  {text}", end="\r", flush=True)

def _ok(text):
    print(f"  {_G}✓{_W}  {text}", flush=True)

def _warn(text):
    print(f"  {_Y}⚠{_W}  {text}", flush=True)

def _err(text):
    print(f"  {_R}✗{_W}  {text}", flush=True)

def _section(title):
    print(f"\n{_B}{title}{_W}")

atexit.register(close_db)

_CONFIDENCE_LEVEL = {"WEAK": 0, "NORMAL": 1, "STRONG": 2}
_last_atr_spot = 0  # cached for mid-cycle vol-exit checks (4H scale)
_last_atr_fut  = 0  # cached for mid-cycle vol-exit checks (1H scale)


def _confidence_at_least(actual, minimum):
    """Return True if actual confidence meets or exceeds the minimum level."""
    return _CONFIDENCE_LEVEL.get(actual, -1) >= _CONFIDENCE_LEVEL.get(minimum, 0)


def _check_reentry_quality(signal, mode):
    """TA-driven re-entry guard: skip if price is worse and confidence didn't improve.

    Compared against the last RESOLVED WIN/LOSS in the same direction (not the
    most recent close of any kind) — a quick FLIP or VOL_EXIT isn't a meaningful
    benchmark for whether the current re-entry has improved.

    Allows re-entry on any of:
      - better entry price (lower for BUY, higher for SELL), or
      - confidence tier upgrade (WEAK → NORMAL → STRONG), or
      - raw strength gain ≥ +0.3 (was +0.5; threshold-scaled — most signals
        only span 5.0–6.5, +0.5 was unreachable without an explicit tier jump).

    Returns (reason: str | None) — None means allow re-entry.
    """
    from trading.history import _conn
    from signals.market_data import get_signal_confidence
    c = _conn()
    row = c.execute(
        """SELECT p.entry_price, p.type, s.strength
           FROM paper_positions p
           LEFT JOIN signals s ON p.signal_id = s.id
           WHERE p.outcome IN ('WIN','LOSS')
             AND p.mode = ?
             AND p.type = ?
           ORDER BY p.closed_at DESC LIMIT 1""",
        (mode, signal["type"]),
    ).fetchone()
    if not row:
        return None  # no resolved history in this direction → allow

    last_entry = row["entry_price"]
    last_strength = row["strength"] or 0
    new_entry = signal["entry_price"]
    new_type = signal["type"]
    new_strength = signal.get("strength", 0)

    # Price improved for BUY (lower) / SELL (higher)
    price_improved = (new_type == "BUY" and new_entry <= last_entry) or \
                     (new_type == "SELL" and new_entry >= last_entry)
    if price_improved:
        return None

    # Confidence tier upgrade — uses current threshold as proxy for the historical
    # one (adaptive thresholds drift slowly; close enough for tier comparison).
    thr = signal.get("_threshold", 0) or 0
    if thr > 0:
        last_conf = get_signal_confidence(last_strength, thr)
        new_conf  = signal.get("confidence") or get_signal_confidence(new_strength, thr)
        if _CONFIDENCE_LEVEL.get(new_conf, -1) > _CONFIDENCE_LEVEL.get(last_conf, -1):
            return None  # tier upgraded → allow

    # Final fallback — significant raw strength gain
    if new_strength >= last_strength + 0.3:
        return None

    return f"entry ${new_entry:,.0f} worse than last ${last_entry:,.0f} with no confidence upgrade ({new_strength:.1f} vs {last_strength:.1f})"


def _is_counter_trend_regime(signal, direction):
    """Block entry when the dominant regime opposes the signal direction.

    Symmetric BUY/SELL — BUY blocked in trending BEARISH, SELL blocked in
    trending BULLISH. RANGING / TRANSITION are not counter-trend; DI flips at
    low ADX are noise, not signals to block on.
    """
    regime = signal.get("_regime", {})
    if regime.get("regime") not in ("TRENDING", "VOLATILE"):
        return False
    trend = regime.get("trend_dir")
    return (direction == "BUY" and trend == "BEARISH") or \
           (direction == "SELL" and trend == "BULLISH")


def _is_bearish_regime(signal):
    """Back-compat wrapper — returns True if BUY would be entering a bearish trend."""
    return _is_counter_trend_regime(signal, "BUY")


def _trend_confluence_for_direction(signal, direction):
    """Require ≥2/3 confirmations matching the signal direction.

    Confirmations: price vs EMA200, ADX trend_dir, price vs VWAP.
    BUY needs all three bullish; SELL needs all three bearish.
    """
    last = signal.get("_last", {})
    regime = signal.get("_regime", {})
    close = last.get("close", 0)
    ema = last.get("ema200", 0)
    vwap = last.get("vwap", 0)
    trend = regime.get("trend_dir")
    score = 0
    if direction == "BUY":
        if ema and close > ema:                  score += 1
        if trend == "BULLISH":                   score += 1
        if vwap and close > vwap:                score += 1
    else:  # SELL
        if ema and close < ema:                  score += 1
        if trend == "BEARISH":                   score += 1
        if vwap and close < vwap:                score += 1
    return score >= 2


def _trend_confluence_ok(signal):
    """Back-compat wrapper — bullish confluence for spot BUY."""
    return _trend_confluence_for_direction(signal, "BUY")


def _is_breakout_chase(signal):
    """Block spot entry if price is far above VWAP and not near support (breakout chase)."""
    last = signal.get("_last", {})
    sr = signal.get("support_resistance", {})
    price = signal.get("entry_price", 0)
    vwap = last.get("vwap", 0)
    atr = signal.get("atr", 0)
    support = sr.get("support", 0)

    if not vwap or not atr:
        return False
    # Breakout chase: price > VWAP by more than 1 ATR AND not near support
    if price > vwap + atr:
        if not support or (price - support) > atr * 1.5:
            return True
    return False


def _get_entry_prices_by_direction(direction, mode):
    """Return list of entry prices for open positions in the given direction, ordered by opened_at."""
    from trading.history import _conn
    c = _conn()
    rows = c.execute(
        "SELECT entry_price FROM paper_positions WHERE outcome IS NULL AND type = ? AND mode = ? ORDER BY opened_at ASC",
        (direction, mode),
    ).fetchall()
    return [r["entry_price"] for r in rows]


def _get_psychology_levels(price, step=1000):
    """Return nearest round-number levels below and above price."""
    base = int(price // step) * step
    return base, base + step


def _check_psychology_sl_risk(entry, sl, step, buffer_pct, direction="BUY"):
    """Return warning if SL is too close to a psychology level (stop-hunt target).

    BUY stops sit BELOW entry — vulnerable to a push DOWN through the nearest
    round number ABOVE the SL (e.g. SL $79,950 with $80k below it = hunt down
    past $80k triggers SL).

    SELL stops sit ABOVE entry — vulnerable to a push UP through the nearest
    round number BELOW the SL (e.g. SL $80,050 with $80k below it = hunt up
    past $80k triggers SL).
    """
    sl_below, sl_above = _get_psychology_levels(sl, step)
    if direction == "BUY":
        dist = (sl_above - sl) / sl * 100
        if dist <= buffer_pct:
            return f"SL ${sl:,.0f} sits {dist:.2f}% below psychology ${sl_above:,.0f} — vulnerable to stop hunt"
    else:  # SELL / SHORT — round below is the magnet
        dist = (sl - sl_below) / sl * 100 if sl > 0 else 0
        if dist <= buffer_pct:
            return f"SL ${sl:,.0f} sits {dist:.2f}% above psychology ${sl_below:,.0f} — vulnerable to stop hunt"
    return None


def _check_psychology_entry_risk(entry, step, buffer_pct):
    """Return warning if entry is just above a psychology level (false breakout risk)."""
    entry_below, entry_above = _get_psychology_levels(entry, step)
    dist = (entry - entry_below) / entry * 100
    if dist <= buffer_pct:
        return f"Entry ${entry:,.0f} only {dist:.2f}% above psychology ${entry_below:,.0f} — false breakout risk"
    return None


def _check_sr_entry_risk(entry, sr, direction, atr, atr_mult=1.0):
    """Check if entry is near resistance (BUY) or support (SELL) — higher reversal risk."""
    if not sr or not atr:
        return None
    threshold = atr * atr_mult
    if direction == "BUY" and sr.get("resistance"):
        dist = sr["resistance"] - entry
        if 0 < dist <= threshold:
            return f"Entry ${entry:,.0f} within {atr_mult}× ATR of resistance ${sr['resistance']:,.0f} — rejection risk elevated"
    elif direction == "SELL" and sr.get("support"):
        dist = entry - sr["support"]
        if 0 < dist <= threshold:
            return f"Entry ${entry:,.0f} within {atr_mult}× ATR of support ${sr['support']:,.0f} — bounce risk elevated"
    return None


def _detect_fakeout_rejection(signal, wick_ratio=0.6):
    """Detect potential fake breakout via wick analysis on 24H range.

    A long upper wick with close near the low = possible fake bullish breakout.
    A long lower wick with close near the high = possible fake bearish breakdown.
    """
    last = signal.get("_last", {})
    hi = last.get("hi24")
    lo = last.get("lo24")
    close = last.get("close")
    if not all([hi, lo, close]) or hi == lo:
        return None

    range_24h = hi - lo
    upper_wick_ratio = (hi - close) / range_24h
    lower_wick_ratio = (close - lo) / range_24h

    if signal["type"] == "BUY" and upper_wick_ratio > wick_ratio:
        return f"Fake bullish breakout: upper wick {(upper_wick_ratio * 100):.0f}% of 24H range — rejection from ${hi:,.0f}"
    if signal["type"] == "SELL" and lower_wick_ratio > wick_ratio:
        return f"Fake bearish breakdown: lower wick {(lower_wick_ratio * 100):.0f}% of 24H range — rejection from ${lo:,.0f}"
    return None


def _calc_aggregate_risk(mode, new_entry_price, new_sl, new_size_factor, pyramid_cfg):
    """Sum the risk % of all open + new position. Returns (total_risk, warning)."""
    max_risk = pyramid_cfg.get("max_aggregate_risk_pct", 5.0)
    total_risk = 0.0

    for pos in get_open_positions(mode):
        entry = pos["entry_price"]
        trail = pos.get("trailing_stop")
        sl = trail if trail is not None else pos.get("stop_loss")
        if sl is None:
            sl = entry
        sf = pos.get("size_factor")
        if sf is None:
            sf = 1.0
        risk = abs(entry - sl) / entry * 100
        total_risk += risk * sf

    # Add the new entry
    new_risk = abs(new_entry_price - new_sl) / new_entry_price * 100
    total_risk += new_risk * new_size_factor

    if total_risk > max_risk:
        return total_risk, f"Aggregate risk {total_risk:.1f}% exceeds max {max_risk:.1f}% — skip to protect account"
    return total_risk, None


def _block(phase3_actions, mode, signal, gate, reason):
    """Log a Phase 3 gate block to DB + logger + phase3_actions in one call.

    Returns None — callers should not branch on the value. Designed to replace
    the 3-line `msg = …; logger.info(msg); phase3_actions.append(…)` pattern
    so every block site automatically persists for later analysis.
    """
    try:
        log_signal_block(
            mode=mode,
            signal_type=signal.get("type", "?") if signal else "?",
            gate=gate, reason=reason,
            strength=signal.get("strength") if signal else None,
            confidence=signal.get("confidence") if signal else None,
            signal_id=signal.get("db_id") if signal else None,
        )
    except Exception as e:
        logger.debug("signal_blocks insert failed: %s", e)
    logger.info(reason)
    label = "SPOT" if mode == "spot" else "FUT "
    phase3_actions.append(f"⏭ {label}  {reason}")


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

def run_cycle():
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    _section(f"SpotSignal · BTC/USDT · {ts}")

    # Phase 1 — scrape news
    _loading("Phase 1  Fetching news & macro data...")
    start = time.time()
    try:
        scrape_and_export()
        _ok(f"Phase 1  News & macro synced  ({time.time()-start:.1f}s)")
    except Exception as e:
        logger.error("Phase 1 failed — proceeding with stale data: %s", e)
        _err(f"Phase 1  Failed — using stale data  ({e})")

    # Phase 2 — analyze
    _loading("Phase 2  SPOT 4H — fetching OHLCV & indicators...")
    spot_signal = None
    try:
        spot_signal = analyze_spot_signal(symbol="BTC/USDT", include_news=True)
        if spot_signal is None:
            raise RuntimeError("analysis returned no signal")
        s_type = spot_signal["type"]
        s_score = spot_signal.get("strength", 0)
        s_icon = "🟢" if s_type == "BUY" else ("🔴" if s_type == "SELL" else "⏸")
        _ok(f"Phase 2  SPOT 4H    {s_icon} {s_type}  score {s_score:.2f}")
    except Exception as e:
        logger.error("Spot analysis failed: %s: %s", type(e).__name__, e)
        _err(f"Phase 2  SPOT failed  ({type(e).__name__}: {e})")

    _loading("Phase 2  FUTURES 1H — fetching OHLCV & indicators...")
    futures_signal = None
    try:
        futures_signal = analyze_futures_signal(symbol="BTC/USDT", include_news=True)
        if futures_signal is None:
            raise RuntimeError("analysis returned no signal")
        f_type = futures_signal["type"]
        f_score = futures_signal.get("strength", 0)
        f_icon = "🟢" if f_type == "BUY" else ("🔴" if f_type == "SELL" else "⏸")
        _ok(f"Phase 2  FUTURES 1H {f_icon} {f_type}  score {f_score:.2f}")
    except Exception as e:
        logger.error("Futures analysis failed: %s: %s", type(e).__name__, e)
        _err(f"Phase 2  FUTURES failed  ({type(e).__name__}: {e})")

    # Phase 3 — paper trading
    _loading("Phase 3  Updating paper positions...")
    phase3_actions = []     # track what happened for summary
    pending_tg    = []      # defer Telegram notifications until after Phase 4

    # ── Circuit breaker: per-mode drawdown + daily loss ──
    # Spot and futures have very different risk profiles. Evaluating combined
    # drawdown can mask a collapsing sub-account ("futures −12% but spot +5%
    # = total −7%" lets futures keep adding). Each mode now blocks itself.
    import trading.history as _h
    spot_pnl, _, _ = _h.get_closed_pnl("spot")
    fut_pnl, _, _ = _h.get_closed_pnl("futures")
    spot_dd = max(0, -spot_pnl)
    fut_dd  = max(0, -fut_pnl)
    total_dd = max(0, -(spot_pnl + fut_pnl))
    max_dd = RISK_LIMITS.get("max_drawdown_pct", 15.0)
    min_eq = RISK_LIMITS.get("min_equity_pct", 50.0)

    daily_spot = _h.get_daily_pnl("spot")
    daily_fut  = _h.get_daily_pnl("futures")
    daily_limit = RISK_LIMITS.get("daily_loss_limit", 5.0)

    spot_block = (spot_dd > max_dd) or (daily_spot < -daily_limit)
    fut_block  = (fut_dd > max_dd) or (daily_fut < -daily_limit)

    def _breaker_reason(mode, dd, daily, dd_lim, daily_lim):
        if daily < -daily_lim:
            return f"daily loss {daily:.1f}% > -{daily_lim}%"
        return f"drawdown {dd:.1f}% > {dd_lim}%"

    if spot_block:
        reason = _breaker_reason("spot", spot_dd, daily_spot, max_dd, daily_limit)
        msg = f"⛔ SPOT  {reason} — blocking new spot entries"
        logger.warning(msg)
        phase3_actions.append(msg)
        if spot_signal:
            spot_signal["type"] = "HOLD"

    if fut_block:
        reason = _breaker_reason("futures", fut_dd, daily_fut, max_dd, daily_limit)
        msg = f"⛔ FUT   {reason} — blocking new futures entries"
        logger.warning(msg)
        phase3_actions.append(msg)
        if futures_signal:
            futures_signal["type"] = "HOLD"

    if spot_block or fut_block:
        modes_blocked = " + ".join(m for m, b in (("SPOT", spot_block), ("FUT", fut_block)) if b)
        pending_tg.append((
            f"⛔ <b>Circuit Breaker</b>\n"
            f"{modes_blocked} new entries blocked\n"
            f"Spot DD <b>{spot_dd:.1f}%</b>  ·  Fut DD <b>{fut_dd:.1f}%</b>\n"
            f"Daily Spot <b>{daily_spot:+.1f}%</b>  ·  Daily Fut <b>{daily_fut:+.1f}%</b>",
            "circuit-breaker",
        ))

    # Combined terminal display (run AFTER the breaker so VERDICT reflects the
    # blocked state — otherwise terminal shows STRONG BUY while Telegram says HOLD).
    display_combined(spot_signal, futures_signal)

    if total_dd > (100 - min_eq):
        msg = f"🚨 EQUITY {100-total_dd:.0f}% < {min_eq}% — EMERGENCY close all"
        logger.critical(msg)
        phase3_actions.append(msg)
        # Use latest signal price for a meaningful exit P&L (was hard-coded 0).
        breaker_px = (futures_signal or spot_signal or {}).get("entry_price")
        for p in _h.get_open_positions():
            if breaker_px:
                breaker_pnl = ((breaker_px - p["entry_price"]) / p["entry_price"] * 100) \
                              if p["type"] == "BUY" \
                              else ((p["entry_price"] - breaker_px) / p["entry_price"] * 100)
            else:
                breaker_pnl = 0
            _h.close_paper_position(p["id"], "BREAKER_CLOSE", round(breaker_pnl, 2), exit_price=breaker_px)

    try:
        # Spot positions — BUY-only (no short selling on spot)
        if spot_signal and spot_signal["type"] != "HOLD" and spot_signal.get("_cached"):
            # Replayed 4H analysis — see signals/spot.py. Acting on it would open
            # at a price up to 3h old, against gates evaluated on that old data.
            _block(phase3_actions, "spot", spot_signal, "stale_cache",
                   f"Spot {spot_signal['type']} from cached 4H analysis — stale entry data, not acted on")

        elif spot_signal and spot_signal["type"] != "HOLD":
            pyramid_cfg = RISK_CONFIG.get("pyramid", {})
            existing_count = get_open_position_count_by_direction(spot_signal["type"], "spot")

            if existing_count == 0:
                # First position — TA-driven quality gates
                min_initial = pyramid_cfg.get("min_initial_confidence", "NORMAL")
                actual_conf = spot_signal.get("confidence")
                fakeout_warn = _detect_fakeout_rejection(
                    spot_signal, wick_ratio=pyramid_cfg.get("fakeout_wick_ratio", 0.6),
                )
                psy_warn = _check_psychology_sl_risk(
                    spot_signal["entry_price"], spot_signal["stop_loss"],
                    pyramid_cfg.get("psychology_level_step", 1000),
                    pyramid_cfg.get("psychology_buffer_pct", 0.15),
                )
                sr_warn = _check_sr_entry_risk(
                    spot_signal["entry_price"],
                    spot_signal.get("support_resistance", {}),
                    spot_signal["type"], spot_signal.get("atr", 0),
                    atr_mult=pyramid_cfg.get("sr_entry_risk_atr", 1.0),
                )

                # Gate 0a: TA-driven re-entry quality (price improved or confidence upgraded)
                reentry_warn = _check_reentry_quality(spot_signal, "spot")
                if reentry_warn:
                    _block(phase3_actions, "spot", spot_signal, "reentry_first", reentry_warn)

                # Gate 0b: initial confidence floor
                elif not _confidence_at_least(actual_conf, min_initial):
                    _block(phase3_actions, "spot", spot_signal, "confidence_first",
                           f"Spot {spot_signal['type']} requires ≥{min_initial} confidence for first entry (got {actual_conf}) — skipping")

                # Gate 0c: fakeout check
                elif fakeout_warn:
                    _block(phase3_actions, "spot", spot_signal, "fakeout_first", fakeout_warn)

                # Gate 0d: psychology-level SL vulnerability
                elif psy_warn:
                    _block(phase3_actions, "spot", spot_signal, "psy_sl_first", psy_warn)

                # Gate 0e: S/R entry proximity risk
                elif sr_warn:
                    _block(phase3_actions, "spot", spot_signal, "sr_first", sr_warn)

                # Gate 0f: regime filter — no BUY in bearish trend
                elif _is_bearish_regime(spot_signal):
                    _block(phase3_actions, "spot", spot_signal, "regime_bearish",
                           "Spot BUY blocked — bearish trend regime")

                # Gate 0g: trend confluence — need 2/3 bullish confirmations
                elif not _trend_confluence_ok(spot_signal):
                    _block(phase3_actions, "spot", spot_signal, "trend_confluence",
                           "Spot BUY blocked — trend confluence < 2/3")

                # Gate 0h: pullback-only — avoid breakout chase
                elif _is_breakout_chase(spot_signal):
                    _block(phase3_actions, "spot", spot_signal, "breakout_chase",
                           "Spot BUY blocked — breakout chase (price above VWAP, not near support)")

                else:
                    pid = open_paper_position(spot_signal, mode="spot", pyramid_entry=1, size_factor=1.0)
                    msg = f"SPOT {spot_signal['type']} opened (#{pid}) @ ${spot_signal['entry_price']:,.0f}"
                    logger.info(msg)
                    phase3_actions.append(f"🚀 {msg}")
                    pending_tg.append((_format_open_notification(spot_signal, pid, "spot"), "position-open"))

            elif pyramid_cfg.get("enabled", False):
                max_entries = pyramid_cfg.get("max_entries", 3)
                min_conf   = pyramid_cfg.get("min_confidence", "STRONG")
                actual_conf = spot_signal.get("confidence")
                entry_prices = _get_entry_prices_by_direction(spot_signal["type"], "spot")
                atr          = spot_signal.get("atr", 0)

                # Safety: bail if positions vanished between queries (macro close race)
                if not entry_prices:
                    _block(phase3_actions, "spot", spot_signal, "positions_cleared",
                           f"Spot {spot_signal['type']} positions cleared — skipping")

                # Gate 1: max entries
                elif existing_count >= max_entries:
                    _block(phase3_actions, "spot", spot_signal, "pyramid_max_entries",
                           f"Spot {spot_signal['type']} pyramid max {max_entries} reached ({existing_count} open) — skipping")

                # Gate 2: confidence (ordinal — STRONG > NORMAL > WEAK)
                elif not _confidence_at_least(actual_conf, min_conf):
                    _block(phase3_actions, "spot", spot_signal, "pyramid_confidence",
                           f"Spot {spot_signal['type']} pyramid requires ≥{min_conf} confidence (got {actual_conf}) — skipping")

                # Gate 3: min distance from last entry (prevent doubling down)
                elif atr <= 0:
                    _block(phase3_actions, "spot", spot_signal, "pyramid_atr_invalid",
                           f"Spot {spot_signal['type']} pyramid ATR invalid ({atr}) — cannot check entry distance")
                elif abs(spot_signal["entry_price"] - entry_prices[-1]) / atr < pyramid_cfg.get("min_entry_distance_atr", 0.5):
                    last_entry = entry_prices[-1]
                    dist_pct = abs(spot_signal["entry_price"] - last_entry) / last_entry * 100
                    min_dist = pyramid_cfg.get("min_entry_distance_atr", 0.5)
                    _block(phase3_actions, "spot", spot_signal, "pyramid_min_distance",
                           f"Spot {spot_signal['type']} pyramid distance {dist_pct:.2f}% (< {min_dist}× ATR) — skipping")

                # Gate 4: max distance from first entry (risk/reward degraded)
                elif abs(spot_signal["entry_price"] - entry_prices[0]) / entry_prices[0] * 100 > pyramid_cfg.get("max_entry_distance_pct", 6.0):
                    first_entry = entry_prices[0]
                    dist_pct = abs(spot_signal["entry_price"] - first_entry) / first_entry * 100
                    max_dist = pyramid_cfg.get("max_entry_distance_pct", 6.0)
                    _block(phase3_actions, "spot", spot_signal, "pyramid_max_distance",
                           f"Spot {spot_signal['type']} pyramid distance from 1st {dist_pct:.1f}% (> {max_dist}%) — skipping")

                # Gate 5a: psychology-level SL vulnerability (stop-hunt risk)
                elif (psy_warn := _check_psychology_sl_risk(
                    spot_signal["entry_price"], spot_signal["stop_loss"],
                    pyramid_cfg.get("psychology_level_step", 1000),
                    pyramid_cfg.get("psychology_buffer_pct", 0.15),
                )):
                    _block(phase3_actions, "spot", spot_signal, "psy_sl_pyramid", psy_warn)

                # Gate 5b: entry just above psychology level (false breakout risk)
                elif (psy_entry_warn := _check_psychology_entry_risk(
                    spot_signal["entry_price"],
                    pyramid_cfg.get("psychology_level_step", 1000),
                    pyramid_cfg.get("psychology_buffer_pct", 0.15),
                )):
                    _block(phase3_actions, "spot", spot_signal, "psy_entry_pyramid", psy_entry_warn)

                # Gate 6: S/R entry risk (entry too close to resistance for BUY)
                elif (sr_warn := _check_sr_entry_risk(
                    spot_signal["entry_price"],
                    spot_signal.get("support_resistance", {}),
                    spot_signal["type"], atr,
                    atr_mult=pyramid_cfg.get("sr_entry_risk_atr", 1.0),
                )):
                    _block(phase3_actions, "spot", spot_signal, "sr_pyramid", sr_warn)

                # Gate 7: fakeout / rejection pattern
                elif (fakeout_warn := _detect_fakeout_rejection(
                    spot_signal, wick_ratio=pyramid_cfg.get("fakeout_wick_ratio", 0.6),
                )):
                    _block(phase3_actions, "spot", spot_signal, "fakeout_pyramid", fakeout_warn)

                else:
                    entry_number = existing_count + 1
                    size_factor = get_pyramid_size_factor(entry_number, pyramid_cfg)
                    min_size = pyramid_cfg.get("min_size_usdt", 10.0)

                    # Tighten SL FIRST so the subsequent size calc reflects the
                    # tighter risk distance — sizing with the original (wide) SL
                    # would under-allocate capital relative to risk_per_trade.
                    # Entry #2: 1.5×ATR → 1.25×ATR, Entry #3: 1.25×ATR → 1.0×ATR (floor)
                    atr_step = pyramid_cfg.get("tighten_sl_atr_step", 0.25)
                    base_atr_mult = RISK_CONFIG.get("atr_multiplier", 1.5)
                    new_atr_mult = max(1.0, base_atr_mult - atr_step * (entry_number - 1))
                    if atr > 0 and new_atr_mult < base_atr_mult:
                        entry_px = spot_signal["entry_price"]
                        new_sl_dist = atr * new_atr_mult
                        spot_signal["stop_loss"] = round(entry_px - new_sl_dist, 2)
                        raw_tp1 = entry_px + new_sl_dist * RISK_CONFIG["take_profit_rr"]
                        raw_tp2 = entry_px + new_sl_dist * RISK_CONFIG["take_profit_rr"] * 2
                        # Cap recomputed TPs at resistance — engine's original
                        # cap requires min 1.0× ATR distance after capping so
                        # TP can't land just above entry (degenerate R/R).
                        resistance = (spot_signal.get("support_resistance") or {}).get("resistance")
                        if resistance:
                            ceiling = resistance * 0.995
                            min_tp_dist = atr * 1.0  # engine uses atr_stop, ours is atr × new_atr_mult≥1.0
                            if entry_px < ceiling < raw_tp1 and (ceiling - entry_px) >= min_tp_dist:
                                raw_tp1 = ceiling
                            if entry_px < ceiling < raw_tp2 and (ceiling - entry_px) >= min_tp_dist:
                                raw_tp2 = ceiling
                        spot_signal["take_profit"] = round(raw_tp1, 2)
                        spot_signal["tp2"] = round(raw_tp2, 2)

                    # Compute size against the (now tightened) SL — keeps the
                    # actual capital-at-risk in line with risk_per_trade.
                    base_size = calculate_position_size(spot_signal)["usdt_amount"]

                    if base_size * size_factor < min_size:
                        _block(phase3_actions, "spot", spot_signal, "pyramid_min_size",
                               f"Spot {spot_signal['type']} pyramid #{entry_number} size ${base_size * size_factor:.2f} < ${min_size:.2f} min — skipping")
                    else:
                        # Gate 8: aggregate risk cap
                        agg_risk, agg_warn = _calc_aggregate_risk(
                            "spot", spot_signal["entry_price"],
                            spot_signal["stop_loss"], size_factor, pyramid_cfg,
                        )
                        if agg_warn:
                            _block(phase3_actions, "spot", spot_signal, "pyramid_aggregate_risk",
                                   f"Spot {spot_signal['type']} pyramid #{entry_number} — {agg_warn}")
                        else:
                            pid = open_paper_position(spot_signal, mode="spot", pyramid_entry=entry_number, size_factor=size_factor)
                            size_note = f" (size {size_factor * 100:.0f}% of base)" if size_factor < 1.0 else ""
                            sl_note = f" [SL {new_atr_mult:.2f}×ATR]" if new_atr_mult < base_atr_mult else ""
                            msg = f"SPOT {spot_signal['type']} pyramid entry #{entry_number} opened (#{pid}) @ ${spot_signal['entry_price']:,.0f}{size_note}{sl_note}"
                            logger.info(msg)
                            phase3_actions.append(f"🧩 {msg}")
                            pending_tg.append((
                                _format_open_notification(spot_signal, pid, "spot", pyramid_entry=entry_number),
                                "position-open",
                            ))
            else:
                # Pyramid disabled — preserve existing skip behaviour
                _block(phase3_actions, "spot", spot_signal, "already_open",
                       f"Already have open {spot_signal['type']} spot — skipping")

        # Futures positions — close-and-flip on opposite signal
        if futures_signal and futures_signal["type"] != "HOLD":
            fut_entry_cfg = FUTURES_CONFIG.get("entry", {})
            actual_conf = futures_signal.get("confidence")
            min_conf = fut_entry_cfg.get("min_confidence", "NORMAL")
            fakeout_warn = _detect_fakeout_rejection(
                futures_signal, wick_ratio=fut_entry_cfg.get("fakeout_wick_ratio", 0.6),
            )
            opp_dir = "SELL" if futures_signal["type"] == "BUY" else "BUY"
            opp_positions = [p for p in get_open_positions("futures") if p["type"] == opp_dir]
            flipped = False

            for opp in opp_positions:
                entry = opp["entry_price"]
                flip_px = futures_signal["entry_price"]
                pnl = ((entry - flip_px) / entry * 100) if opp_dir == "SELL" else ((flip_px - entry) / entry * 100)
                close_paper_position(opp["id"], "FLIP", round(pnl, 2), exit_price=flip_px)
                msg = f"FUT {opp['type']} flipped → {futures_signal['type']} (#{opp['id']} closed, P&L {pnl:+.2f}%)"
                logger.info(msg)
                phase3_actions.append(f"🔄 {msg}")
                flipped = True

            if flipped:
                # Flip profitability: only open if new signal can recover the loss
                flip_pnl_total = sum(
                    ((p["entry_price"] - flip_px) / p["entry_price"] * 100) if opp_dir == "SELL"
                    else ((flip_px - p["entry_price"]) / p["entry_price"] * 100)
                    for p in opp_positions
                )
                new_sl = futures_signal.get("stop_loss", flip_px)
                new_tp = futures_signal.get("take_profit", flip_px)
                expected_reward = abs(new_tp - flip_px) / flip_px * 100 if flip_px > 0 else 0

                # Same quality gates as first-entry — a flip is opening a new
                # position; if the regime opposes or structure is weak the flip
                # should be skipped even though we already closed the opposite.
                fut_psy_step = RISK_CONFIG.get("pyramid", {}).get("psychology_level_step", 1000)
                fut_psy_buf  = RISK_CONFIG.get("pyramid", {}).get("psychology_buffer_pct", 0.15)
                fut_sr_atr   = RISK_CONFIG.get("pyramid", {}).get("sr_entry_risk_atr", 1.0)
                flip_psy_warn = _check_psychology_sl_risk(
                    futures_signal["entry_price"], futures_signal["stop_loss"],
                    fut_psy_step, fut_psy_buf, direction=futures_signal["type"],
                )
                flip_sr_warn = _check_sr_entry_risk(
                    futures_signal["entry_price"],
                    futures_signal.get("support_resistance", {}),
                    futures_signal["type"], futures_signal.get("atr", 0),
                    atr_mult=fut_sr_atr,
                )

                # Gate: skip flip if loss > expected reward (can't recover)
                if flip_pnl_total < -expected_reward:
                    _block(phase3_actions, "futures", futures_signal, "flip_unprofitable",
                           f"FUT flip blocked: loss {flip_pnl_total:.2f}% > expected reward {expected_reward:.2f}% — skipping open")
                elif not _confidence_at_least(actual_conf, min_conf):
                    _block(phase3_actions, "futures", futures_signal, "flip_confidence",
                           f"FUT flip requires ≥{min_conf} confidence (got {actual_conf}) — skipping open")
                elif fakeout_warn:
                    _block(phase3_actions, "futures", futures_signal, "flip_fakeout", fakeout_warn)
                elif flip_psy_warn:
                    _block(phase3_actions, "futures", futures_signal, "flip_psy_sl", flip_psy_warn)
                elif flip_sr_warn:
                    _block(phase3_actions, "futures", futures_signal, "flip_sr", flip_sr_warn)
                elif _is_counter_trend_regime(futures_signal, futures_signal["type"]):
                    direction_word = "bullish" if futures_signal["type"] == "SELL" else "bearish"
                    _block(phase3_actions, "futures", futures_signal, "flip_regime_counter",
                           f"FUT {futures_signal['type']} flip blocked — counter-trend {direction_word} regime")
                elif not _trend_confluence_for_direction(futures_signal, futures_signal["type"]):
                    _block(phase3_actions, "futures", futures_signal, "flip_trend_confluence",
                           f"FUT {futures_signal['type']} flip blocked — trend confluence < 2/3")
                else:
                    pid = open_paper_position(futures_signal, mode="futures")
                    msg = f"FUT {futures_signal['type']} opened (#{pid}) @ ${futures_signal['entry_price']:,.0f} (flip)"
                    logger.info(msg)
                    phase3_actions.append(f"🔄 {msg}")
                    pending_tg.append((_format_open_notification(futures_signal, pid, "futures"), "position-open"))

            elif has_open_position_same_direction(futures_signal["type"], "futures"):
                _block(phase3_actions, "futures", futures_signal, "already_open",
                       f"Already have open {futures_signal['type']} futures — skipping")

            else:
                # First futures entry — quality gates
                reentry_warn = _check_reentry_quality(futures_signal, "futures") \
                    if fut_entry_cfg.get("reentry_price_check", True) else None
                # Reuse pyramid_cfg buffers for psy/sr — same magnitudes apply across modes
                fut_psy_step  = RISK_CONFIG.get("pyramid", {}).get("psychology_level_step", 1000)
                fut_psy_buf   = RISK_CONFIG.get("pyramid", {}).get("psychology_buffer_pct", 0.15)
                fut_sr_atr    = RISK_CONFIG.get("pyramid", {}).get("sr_entry_risk_atr", 1.0)
                fut_psy_warn  = _check_psychology_sl_risk(
                    futures_signal["entry_price"], futures_signal["stop_loss"],
                    fut_psy_step, fut_psy_buf, direction=futures_signal["type"],
                )
                fut_sr_warn = _check_sr_entry_risk(
                    futures_signal["entry_price"],
                    futures_signal.get("support_resistance", {}),
                    futures_signal["type"], futures_signal.get("atr", 0),
                    atr_mult=fut_sr_atr,
                )

                if reentry_warn:
                    _block(phase3_actions, "futures", futures_signal, "reentry_first", reentry_warn)
                elif not _confidence_at_least(actual_conf, min_conf):
                    _block(phase3_actions, "futures", futures_signal, "confidence_first",
                           f"FUT {futures_signal['type']} requires ≥{min_conf} confidence (got {actual_conf}) — skipping")
                elif fakeout_warn:
                    _block(phase3_actions, "futures", futures_signal, "fakeout_first", fakeout_warn)
                elif fut_psy_warn:
                    _block(phase3_actions, "futures", futures_signal, "psy_sl_first", fut_psy_warn)
                elif fut_sr_warn:
                    _block(phase3_actions, "futures", futures_signal, "sr_first", fut_sr_warn)
                elif _is_counter_trend_regime(futures_signal, futures_signal["type"]):
                    direction_word = "bullish" if futures_signal["type"] == "SELL" else "bearish"
                    _block(phase3_actions, "futures", futures_signal, "regime_counter",
                           f"FUT {futures_signal['type']} blocked — counter-trend {direction_word} regime")
                elif not _trend_confluence_for_direction(futures_signal, futures_signal["type"]):
                    _block(phase3_actions, "futures", futures_signal, "trend_confluence",
                           f"FUT {futures_signal['type']} blocked — trend confluence < 2/3")
                else:
                    # Aggregate risk cap
                    agg_risk, agg_warn = _calc_aggregate_risk(
                        "futures", futures_signal["entry_price"],
                        futures_signal["stop_loss"], 1.0,
                        {"max_aggregate_risk_pct": fut_entry_cfg.get("max_aggregate_risk_pct", 8.0)},
                    )
                    if agg_warn:
                        _block(phase3_actions, "futures", futures_signal, "aggregate_risk",
                               f"FUT {futures_signal['type']} — {agg_warn}")
                    else:
                        pid = open_paper_position(futures_signal, mode="futures")
                        msg = f"FUT {futures_signal['type']} opened (#{pid}) @ ${futures_signal['entry_price']:,.0f}"
                        logger.info(msg)
                        phase3_actions.append(f"🚀 {msg}")
                        pending_tg.append((_format_open_notification(futures_signal, pid, "futures"), "position-open"))

        # Determine current price for position checks
        if futures_signal and futures_signal.get("entry_price"):
            current_price = futures_signal["entry_price"]
        elif spot_signal and spot_signal.get("entry_price"):
            current_price = spot_signal["entry_price"]
        else:
            from signals.market_data import exchange
            ticker = exchange.fetch_ticker("BTC/USDT")
            current_price = ticker["last"]

        # Per-mode ATR — spot 4H ATR is ~2× futures 1H ATR, so a shared value
        # triggers spurious VOL_EXITs on futures positions opened at 1H ATR.
        spot_atr = (spot_signal or {}).get("atr", 0)
        fut_atr  = (futures_signal or {}).get("atr", 0)
        if spot_atr > 0:
            global _last_atr_spot; _last_atr_spot = spot_atr
        if fut_atr > 0:
            global _last_atr_fut; _last_atr_fut = fut_atr
        fut_funding = 0
        if futures_signal and futures_signal.get("_market"):
            fut_funding = futures_signal["_market"].get("funding", {}).get("rate_pct", 0)
        closed_spot = check_and_close_positions(current_price, mode="spot", current_atr=spot_atr)
        closed_fut   = check_and_close_positions(current_price, mode="futures", current_atr=fut_atr, funding_rate=fut_funding)
        all_closed   = (closed_spot or []) + (closed_fut or [])

        print_open_status("spot")
        print_open_status("futures")
        print_paper_summary("spot")
        print_paper_summary("futures")

        if phase3_actions:
            _section("Phase 3  Positions")
            # Actions already carry their own leading icon (🚀 / 🧩 / 🔄 / ⏭ / ⛔ / 🚨)
            # Strip the redundant prefix-icon prepend that produced lines like
            # "⏭ ⛔ SPOT  drawdown ..." with two icons.
            self_iconed = ("🚀", "🧩", "🔄", "⏭", "⛔", "🚨")
            for action in phase3_actions:
                if action.startswith(self_iconed):
                    if action[:1] in "🚀🧩🔄":
                        color = _G
                    elif action[:1] in "⛔🚨":
                        color = _R
                    else:
                        color = _DIM
                    print(f"  {color}{action}{_W}")
                else:
                    print(f"  {_DIM}⏭{_W}  {action}")
        else:
            _ok("Phase 3  No position changes")

        # Collect close notification (defer to after Phase 4)
        if all_closed:
            close_msg = _format_close_notification(all_closed)
            if close_msg:
                pending_tg.append((close_msg, "position-close"))

    except Exception as e:
        logger.error("Paper trading update failed: %s", e)
        _err(f"Phase 3  Failed  ({e})")

    # Phase 4 — notifications (defensive: a Telegram hiccup should never
    # crash the cycle loop. The send helpers already catch HTTP errors, but
    # a malformed signal could still raise during formatting.)
    _loading("Phase 4  Sending Telegram notifications...")
    try:
        sent = send_signal_alert(spot_signal=spot_signal, futures_signal=futures_signal) or 0
        for msg, label in pending_tg:
            if _send_telegram_message(msg, label):
                sent += 1
        if sent:
            _ok(f"Phase 4  Telegram sent  ({sent} message{'' if sent == 1 else 's'})")
        elif spot_signal is None and futures_signal is None and not pending_tg:
            _warn("Phase 4  Skipped — no signal to report")
        else:
            _warn("Phase 4  Nothing delivered — check TELEGRAM_* in .env / spotsignal.log")
    except Exception as e:
        logger.error("Phase 4 failed: %s: %s", type(e).__name__, e)
        _err(f"Phase 4  Failed  ({type(e).__name__}: {e})")


# ---------------------------------------------------------------------------
# Mid-cycle position check (no analysis, just trailing stop + close)
# ---------------------------------------------------------------------------

def run_position_check():
    """Light check at half-interval — fetch price, update stops, close if hit."""
    logger.info("[CHECK] Mid-cycle position update ...")
    try:
        from signals.market_data import exchange
        ticker = exchange.fetch_ticker("BTC/USDT")
        price = ticker["last"]

        print(f"\n  {'─' * 40}")
        print(f"  MID-CYCLE CHECK  ·  BTC ${price:,.0f}")
        print(f"  {'─' * 40}")

        closed_spot = check_and_close_positions(price, mode="spot", current_atr=_last_atr_spot)
        closed_fut = check_and_close_positions(price, mode="futures", current_atr=_last_atr_fut)
        all_closed = (closed_spot or []) + (closed_fut or [])

        print_open_status("spot")
        print_open_status("futures")

        if all_closed:
            close_msg = _format_close_notification(all_closed)
            if close_msg:
                _send_telegram_message(close_msg, "position-close")
    except Exception as e:
        logger.error("Position check failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SpotSignal BTC Trading Bot")
    parser.add_argument(
        "--loop", type=int, default=0, metavar="MINUTES",
        help="Run continuously every N minutes (omit to run once).",
    )
    args = parser.parse_args()

    if args.loop > 0:
        full_minute = 1   # full cycle fires at :01 past the hour
        # Mid-cycle check only makes sense when loop ≤ 60 — for longer intervals
        # (loop // 2 ≥ 60) the mod-60 wrap collides full_minute with check_minute,
        # so positions would only get updated on full cycles. Disable cleanly.
        half = args.loop // 2
        if half < 1 or half >= 60:
            check_minute = None
        else:
            check_minute = (full_minute + half) % 60
            if check_minute == full_minute:
                check_minute = None  # still colliding (e.g. loop=60×k → both at :01)

        if check_minute is None:
            _ok(f"Loop mode — full cycle at :{full_minute:02d} (no mid-cycle check; loop={args.loop}min)  (Ctrl+C to stop)")
        else:
            _ok(f"Loop mode — full cycle at :{full_minute:02d}, position check at :{check_minute:02d}  (Ctrl+C to stop)")
        fired = set()
        last_minute = None
        # Set of minutes that should trigger something — filters out everything else.
        active_minutes = {full_minute} if check_minute is None else {full_minute, check_minute}
        while True:
            now = datetime.now().astimezone()
            minute = now.minute

            # Clear fired when the clock minute changes (new minute = new chance)
            if minute != last_minute:
                fired.clear()
                last_minute = minute

            if minute not in active_minutes:
                time.sleep(1)
                continue
            if minute in fired:
                time.sleep(1)
                continue  # already fired this minute

            # Wrap each cycle in try/except so a single failing cycle (network
            # blip, malformed signal, etc.) doesn't kill the long-running loop.
            try:
                if minute == full_minute:
                    run_cycle()
                    next_min = check_minute if check_minute is not None else full_minute
                else:
                    run_position_check()
                    next_min = full_minute
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.exception("Cycle failed: %s", e)
                _err(f"Cycle failed: {e}")
                next_min = check_minute if (minute == full_minute and check_minute is not None) else full_minute
            wait_sec = ((next_min - datetime.now().astimezone().minute - 1) % 60) * 60 + (60 - datetime.now().astimezone().second)
            fired.add(minute)
            _step(_DIM + "⟳" + _W, f"Next run at :{next_min:02d}  (~{max(1, wait_sec // 60)} min)")
    else:
        run_cycle()


if __name__ == "__main__":
    main()
