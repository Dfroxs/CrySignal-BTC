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
from trading.history import close as close_db, close_paper_position, get_open_position_count_by_direction, get_open_positions, has_open_position_same_direction, open_paper_position

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
_last_atr = 0  # cached for mid-cycle vol-exit checks


def _confidence_at_least(actual, minimum):
    """Return True if actual confidence meets or exceeds the minimum level."""
    return _CONFIDENCE_LEVEL.get(actual, -1) >= _CONFIDENCE_LEVEL.get(minimum, 0)


def _check_reentry_quality(signal, mode):
    """TA-driven re-entry guard: skip if price is worse and confidence didn't improve.

    Returns (reason: str | None) — None means allow re-entry.
    """
    from trading.history import _conn
    c = _conn()
    row = c.execute(
        """SELECT p.entry_price, p.type, s.strength
           FROM paper_positions p
           JOIN signals s ON p.signal_id = s.id
           WHERE p.outcome IS NOT NULL AND p.mode = ?
           ORDER BY p.closed_at DESC LIMIT 1""",
        (mode,),
    ).fetchone()
    if not row:
        return None  # no history → allow

    last_entry = row["entry_price"]
    last_type = row["type"]
    last_strength = row["strength"] or 0
    new_entry = signal["entry_price"]
    new_type = signal["type"]
    new_strength = signal.get("strength", 0)

    # Different direction → always allow
    if new_type != last_type:
        return None

    # Price improved for BUY (lower) / SELL (higher)
    price_improved = (new_type == "BUY" and new_entry <= last_entry) or \
                     (new_type == "SELL" and new_entry >= last_entry)

    if price_improved:
        return None  # better entry price → allow

    # Price is worse — require strength upgrade (at least +0.5)
    if new_strength >= last_strength + 0.5:
        return None  # significantly stronger signal → allow

    return f"entry ${new_entry:,.0f} worse than last ${last_entry:,.0f} with no confidence upgrade ({new_strength:.1f} vs {last_strength:.1f})"


def _is_bearish_regime(signal):
    """Block spot BUY in TRENDING or VOLATILE bearish regimes.
    Ranging/transition markets with temporary DI- dominance are not blocked —
    DI- > DI+ at low ADX is normal oscillation, not a trend."""
    regime = signal.get("_regime", {})
    return (regime.get("regime") in ("TRENDING", "VOLATILE") and
            regime.get("trend_dir") == "BEARISH")


def _trend_confluence_ok(signal):
    """Require at least 2 of 3 bullish confirmations for spot entry."""
    last = signal.get("_last", {})
    regime = signal.get("_regime", {})
    score = 0
    # 1. EMA200 bullish
    if last.get("close", 0) > last.get("ema200", 0):
        score += 1
    # 2. ADX trend bullish
    if regime.get("trend_dir") == "BULLISH":
        score += 1
    # 3. Price above VWAP
    if last.get("close", 0) > last.get("vwap", 0):
        score += 1
    return score >= 2


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


def _check_psychology_sl_risk(entry, sl, step, buffer_pct):
    """Return warning if SL is too close to a psychology level (stop-hunt target).

    Checks distance from SL to the nearest round number ABOVE it —
    the level that price must break to hit the stop.
    """
    sl_below, sl_above = _get_psychology_levels(sl, step)
    # Distance from SL up to the next round thousand (the level above)
    dist_to_above = (sl_above - sl) / sl * 100
    if dist_to_above <= buffer_pct:
        return f"SL ${sl:,.0f} sits {dist_to_above:.2f}% below psychology ${sl_above:,.0f} — vulnerable to stop hunt"
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


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

def run_cycle():
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    _section(f"CrySignal · BTC/USDT · {ts}")

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
        s_type = spot_signal["type"]
        s_score = spot_signal.get("strength", 0)
        s_icon = "🟢" if s_type == "BUY" else ("🔴" if s_type == "SELL" else "⏸")
        _ok(f"Phase 2  SPOT 4H    {s_icon} {s_type}  score {s_score:.2f}")
    except Exception as e:
        logger.error("Spot analysis failed: %s", e)
        _err(f"Phase 2  SPOT failed  ({e})")

    _loading("Phase 2  FUTURES 1H — fetching OHLCV & indicators...")
    futures_signal = None
    try:
        futures_signal = analyze_futures_signal(symbol="BTC/USDT", include_news=True)
        f_type = futures_signal["type"]
        f_score = futures_signal.get("strength", 0)
        f_icon = "🟢" if f_type == "BUY" else ("🔴" if f_type == "SELL" else "⏸")
        _ok(f"Phase 2  FUTURES 1H {f_icon} {f_type}  score {f_score:.2f}")
    except Exception as e:
        logger.error("Futures analysis failed: %s", e)
        _err(f"Phase 2  FUTURES failed  ({e})")

    # Combined terminal display (once, not per-mode)
    display_combined(spot_signal, futures_signal)

    # Phase 3 — paper trading
    _loading("Phase 3  Updating paper positions...")
    phase3_actions = []     # track what happened for summary
    pending_tg    = []      # defer Telegram notifications until after Phase 4

    # ── Circuit breaker: check drawdown before any new entries ──
    import trading.history as _h
    spot_pnl, _, _ = _h.get_closed_pnl("spot")
    fut_pnl, _, _ = _h.get_closed_pnl("futures")
    total_dd = -(spot_pnl + fut_pnl)  # negative P&L = positive drawdown
    max_dd = RISK_LIMITS.get("max_drawdown_pct", 15.0)
    min_eq = RISK_LIMITS.get("min_equity_pct", 50.0)

    daily_pnl = _h.get_daily_pnl()
    daily_limit = RISK_LIMITS.get("daily_loss_limit", 5.0)
    daily_breaker = daily_pnl < -daily_limit

    if total_dd > max_dd or daily_breaker:
        if daily_breaker:
            msg = f"⛔ DAILY LOSS {daily_pnl:.1f}% > -{daily_limit}% limit — blocking new entries for today"
        else:
            msg = f"⛔ DRAWDOWN {total_dd:.1f}% > {max_dd}% — blocking new entries"
        logger.warning(msg)
        phase3_actions.append(msg)
        pending_tg.append((
            f"⛔ <b>Circuit Breaker</b>\n"
            f"{'Daily loss' if daily_breaker else 'Drawdown'} "
            f"<b>{abs(daily_pnl if daily_breaker else total_dd):.1f}%</b> > limit\n"
            f"All new entries blocked.",
            "circuit-breaker",
        ))
        # Skip Phase 3 entirely — just run position checks (close existing)
        if spot_signal:
            spot_signal["type"] = "HOLD"
        if futures_signal:
            futures_signal["type"] = "HOLD"

    if total_dd > (100 - min_eq):
        msg = f"🚨 EQUITY {100-total_dd:.0f}% < {min_eq}% — EMERGENCY close all"
        logger.critical(msg)
        phase3_actions.append(msg)
        all_open = _h.get_open_positions()
        for p in all_open:
            _h.close_paper_position(p["id"], "BREAKER_CLOSE", 0)

    try:
        # Spot positions — BUY-only (no short selling on spot)
        if spot_signal and spot_signal["type"] != "HOLD":
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
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], reentry_warn)
                    phase3_actions.append(f"⏭ SPOT  {reentry_warn}")

                # Gate 0b: initial confidence floor
                elif not _confidence_at_least(actual_conf, min_initial):
                    msg = f"Spot {spot_signal['type']} requires ≥{min_initial} confidence for first entry (got {actual_conf}) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 0c: fakeout check
                elif fakeout_warn:
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], fakeout_warn)
                    phase3_actions.append(f"⏭ SPOT  {fakeout_warn}")

                # Gate 0d: psychology-level SL vulnerability
                elif psy_warn:
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], psy_warn)
                    phase3_actions.append(f"⏭ SPOT  {psy_warn}")

                # Gate 0e: S/R entry proximity risk
                elif sr_warn:
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], sr_warn)
                    phase3_actions.append(f"⏭ SPOT  {sr_warn}")

                # Gate 0f: regime filter — no BUY in bearish trend
                elif _is_bearish_regime(spot_signal):
                    msg = f"Spot BUY blocked — bearish trend regime"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 0g: trend confluence — need 2/3 bullish confirmations
                elif not _trend_confluence_ok(spot_signal):
                    msg = f"Spot BUY blocked — trend confluence < 2/3"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 0h: pullback-only — avoid breakout chase
                elif _is_breakout_chase(spot_signal):
                    msg = f"Spot BUY blocked — breakout chase (price above VWAP, not near support)"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

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
                    msg = f"Spot {spot_signal['type']} positions cleared — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 1: max entries
                elif existing_count >= max_entries:
                    msg = f"Spot {spot_signal['type']} pyramid max {max_entries} reached ({existing_count} open) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 2: confidence (ordinal — STRONG > NORMAL > WEAK)
                elif not _confidence_at_least(actual_conf, min_conf):
                    msg = f"Spot {spot_signal['type']} pyramid requires ≥{min_conf} confidence (got {actual_conf}) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 3: min distance from last entry (prevent doubling down)
                elif atr <= 0:
                    msg = f"Spot {spot_signal['type']} pyramid ATR invalid ({atr}) — cannot check entry distance"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")
                elif abs(spot_signal["entry_price"] - entry_prices[-1]) / atr < pyramid_cfg.get("min_entry_distance_atr", 0.5):
                    last_entry = entry_prices[-1]
                    dist_pct = abs(spot_signal["entry_price"] - last_entry) / last_entry * 100
                    min_dist = pyramid_cfg.get("min_entry_distance_atr", 0.5)
                    msg = f"Spot {spot_signal['type']} pyramid distance {dist_pct:.2f}% (< {min_dist}× ATR) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 4: max distance from first entry (risk/reward degraded)
                elif abs(spot_signal["entry_price"] - entry_prices[0]) / entry_prices[0] * 100 > pyramid_cfg.get("max_entry_distance_pct", 6.0):
                    first_entry = entry_prices[0]
                    dist_pct = abs(spot_signal["entry_price"] - first_entry) / first_entry * 100
                    max_dist = pyramid_cfg.get("max_entry_distance_pct", 6.0)
                    msg = f"Spot {spot_signal['type']} pyramid distance from 1st {dist_pct:.1f}% (> {max_dist}%) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ SPOT  {msg}")

                # Gate 5a: psychology-level SL vulnerability (stop-hunt risk)
                elif (psy_warn := _check_psychology_sl_risk(
                    spot_signal["entry_price"], spot_signal["stop_loss"],
                    pyramid_cfg.get("psychology_level_step", 1000),
                    pyramid_cfg.get("psychology_buffer_pct", 0.15),
                )):
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], psy_warn)
                    phase3_actions.append(f"⏭ SPOT  {psy_warn}")

                # Gate 5b: entry just above psychology level (false breakout risk)
                elif (psy_entry_warn := _check_psychology_entry_risk(
                    spot_signal["entry_price"],
                    pyramid_cfg.get("psychology_level_step", 1000),
                    pyramid_cfg.get("psychology_buffer_pct", 0.15),
                )):
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], psy_entry_warn)
                    phase3_actions.append(f"⏭ SPOT  {psy_entry_warn}")

                # Gate 6: S/R entry risk (entry too close to resistance for BUY)
                elif (sr_warn := _check_sr_entry_risk(
                    spot_signal["entry_price"],
                    spot_signal.get("support_resistance", {}),
                    spot_signal["type"], atr,
                    atr_mult=pyramid_cfg.get("sr_entry_risk_atr", 1.0),
                )):
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], sr_warn)
                    phase3_actions.append(f"⏭ SPOT  {sr_warn}")

                # Gate 7: fakeout / rejection pattern
                elif (fakeout_warn := _detect_fakeout_rejection(
                    spot_signal, wick_ratio=pyramid_cfg.get("fakeout_wick_ratio", 0.6),
                )):
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], fakeout_warn)
                    phase3_actions.append(f"⏭ SPOT  {fakeout_warn}")

                else:
                    entry_number = existing_count + 1
                    size_factor = get_pyramid_size_factor(entry_number, pyramid_cfg)
                    min_size = pyramid_cfg.get("min_size_usdt", 10.0)
                    # Compute base size BEFORE SL tightening to get correct sizing
                    base_size = calculate_position_size(spot_signal)["usdt_amount"]

                    if base_size * size_factor < min_size:
                        msg = f"Spot {spot_signal['type']} pyramid #{entry_number} size ${base_size * size_factor:.2f} < ${min_size:.2f} min — skipping"
                        logger.info(msg)
                        phase3_actions.append(f"⏭ SPOT  {msg}")
                    else:
                        # Tighten SL for pyramid entries using additive ATR step
                        # Entry #2: 1.5×ATR → 1.25×ATR, Entry #3: 1.25×ATR → 1.0×ATR (floor)
                        atr_step = pyramid_cfg.get("tighten_sl_atr_step", 0.25)
                        base_atr_mult = RISK_CONFIG.get("atr_multiplier", 1.5)
                        new_atr_mult = max(1.0, base_atr_mult - atr_step * (entry_number - 1))
                        if atr > 0 and new_atr_mult < base_atr_mult:
                            new_sl_dist = atr * new_atr_mult
                            new_sl = spot_signal["entry_price"] - new_sl_dist
                            spot_signal["stop_loss"] = round(new_sl, 2)
                            spot_signal["take_profit"] = round(spot_signal["entry_price"] + new_sl_dist * RISK_CONFIG["take_profit_rr"], 2)
                            spot_signal["tp2"] = round(spot_signal["entry_price"] + new_sl_dist * RISK_CONFIG["take_profit_rr"] * 2, 2)

                        # Gate 8: aggregate risk cap
                        agg_risk, agg_warn = _calc_aggregate_risk(
                            "spot", spot_signal["entry_price"],
                            spot_signal["stop_loss"], size_factor, pyramid_cfg,
                        )
                        if agg_warn:
                            msg = f"Spot {spot_signal['type']} pyramid #{entry_number} — {agg_warn}"
                            logger.info(msg)
                            phase3_actions.append(f"⏭ SPOT  {msg}")
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
                msg = f"Already have open {spot_signal['type']} spot — skipping"
                logger.info(msg)
                phase3_actions.append(f"⏭ SPOT  {msg}")

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
                close_paper_position(opp["id"], "FLIP", round(pnl, 2), closed_at=flip_px)
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

                # Gate: skip flip if loss > expected reward (can't recover)
                if flip_pnl_total < -expected_reward:
                    msg = f"FUT flip blocked: loss {flip_pnl_total:.2f}% > expected reward {expected_reward:.2f}% — skipping open"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ FUT  {msg}")
                elif not _confidence_at_least(actual_conf, min_conf):
                    msg = f"FUT flip requires ≥{min_conf} confidence (got {actual_conf}) — skipping open"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ FUT  {msg}")
                elif fakeout_warn:
                    logger.info("FUT %s — %s — skipping open", futures_signal['type'], fakeout_warn)
                    phase3_actions.append(f"⏭ FUT  {fakeout_warn}")
                else:
                    pid = open_paper_position(futures_signal, mode="futures")
                    msg = f"FUT {futures_signal['type']} opened (#{pid}) @ ${futures_signal['entry_price']:,.0f} (flip)"
                    logger.info(msg)
                    phase3_actions.append(f"🔄 {msg}")
                    pending_tg.append((_format_open_notification(futures_signal, pid, "futures"), "position-open"))

            elif has_open_position_same_direction(futures_signal["type"], "futures"):
                msg = f"Already have open {futures_signal['type']} futures — skipping"
                logger.info(msg)
                phase3_actions.append(f"⏭ FUT  {msg}")

            else:
                # First futures entry — quality gates
                reentry_warn = _check_reentry_quality(futures_signal, "futures") \
                    if fut_entry_cfg.get("reentry_price_check", True) else None
                if reentry_warn:
                    logger.info("FUT %s — %s — skipping", futures_signal['type'], reentry_warn)
                    phase3_actions.append(f"⏭ FUT  {reentry_warn}")
                elif not _confidence_at_least(actual_conf, min_conf):
                    msg = f"FUT {futures_signal['type']} requires ≥{min_conf} confidence (got {actual_conf}) — skipping"
                    logger.info(msg)
                    phase3_actions.append(f"⏭ FUT  {msg}")
                elif fakeout_warn:
                    logger.info("FUT %s — %s — skipping", futures_signal['type'], fakeout_warn)
                    phase3_actions.append(f"⏭ FUT  {fakeout_warn}")
                else:
                    # Aggregate risk cap
                    agg_risk, agg_warn = _calc_aggregate_risk(
                        "futures", futures_signal["entry_price"],
                        futures_signal["stop_loss"], 1.0,
                        {"max_aggregate_risk_pct": fut_entry_cfg.get("max_aggregate_risk_pct", 8.0)},
                    )
                    if agg_warn:
                        msg = f"FUT {futures_signal['type']} — {agg_warn}"
                        logger.info(msg)
                        phase3_actions.append(f"⏭ FUT  {msg}")
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

        current_atr = (spot_signal or futures_signal or {}).get("atr", 0)
        if current_atr > 0:
            global _last_atr; _last_atr = current_atr
        fut_funding = 0
        if futures_signal and futures_signal.get("_market"):
            fut_funding = futures_signal["_market"].get("funding", {}).get("rate_pct", 0)
        closed_spot = check_and_close_positions(current_price, mode="spot", current_atr=current_atr)
        closed_fut   = check_and_close_positions(current_price, mode="futures", current_atr=current_atr, funding_rate=fut_funding)
        all_closed   = (closed_spot or []) + (closed_fut or [])

        print_open_status("spot")
        print_open_status("futures")
        print_paper_summary("spot")
        print_paper_summary("futures")

        if phase3_actions:
            _section("Phase 3  Positions")
            for action in phase3_actions:
                icon = "✓" if action.startswith("🚀") or action.startswith("🧩") or action.startswith("🔄") else "⏭"
                color = _G if icon == "✓" else _DIM
                print(f"  {color}{icon}{_W}  {action}")
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

    # Phase 4 — notifications
    _loading("Phase 4  Sending Telegram notifications...")
    send_signal_alert(spot_signal=spot_signal, futures_signal=futures_signal)
    for msg, label in pending_tg:
        _send_telegram_message(msg, label)
    _ok("Phase 4  Telegram sent")


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

        closed_spot = check_and_close_positions(price, mode="spot", current_atr=_last_atr)
        closed_fut = check_and_close_positions(price, mode="futures", current_atr=_last_atr)
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
        half = args.loop // 2
        full_minute = 1   # full cycle fires at :01 past the hour
        check_minute = (full_minute + half) % 60

        _ok(f"Loop mode — full cycle at :{full_minute:02d}, position check at :{check_minute:02d}  (Ctrl+C to stop)")
        fired = set()
        last_minute = None
        while True:
            now = datetime.now().astimezone()
            minute = now.minute

            # Clear fired when the clock minute changes (new minute = new chance)
            if minute != last_minute:
                fired.clear()
                last_minute = minute

            if minute not in (full_minute, check_minute):
                time.sleep(1)
                continue
            if minute in fired:
                time.sleep(1)
                continue  # already fired this minute

            if minute == full_minute:
                run_cycle()
                next_min = check_minute
                wait_sec = ((next_min - datetime.now().astimezone().minute - 1) % 60) * 60 + (60 - datetime.now().astimezone().second)
            else:
                run_position_check()
                next_min = full_minute
                wait_sec = ((next_min - datetime.now().astimezone().minute - 1) % 60) * 60 + (60 - datetime.now().astimezone().second)
            fired.add(minute)
            _step(_DIM + "⟳" + _W, f"Next run at :{next_min:02d}  (~{max(1, wait_sec // 60)} min)")
    else:
        run_cycle()


if __name__ == "__main__":
    main()
