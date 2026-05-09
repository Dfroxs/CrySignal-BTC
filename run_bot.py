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
from config import RISK_CONFIG, FUTURES_CONFIG
from signals.sizing import calculate_position_size, get_pyramid_size_factor
from trading.history import close as close_db, close_paper_position, get_open_position_count_by_direction, get_open_positions, has_open_position_same_direction, open_paper_position

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("spotsignal.log"),
    ],
)
logger = logging.getLogger(__name__)

atexit.register(close_db)

_CONFIDENCE_LEVEL = {"WEAK": 0, "NORMAL": 1, "STRONG": 2}


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
    # Phase 1 — scrape news
    logger.info("[PHASE 1] Updating market intelligence ...")
    start = time.time()
    try:
        scrape_and_export()
        logger.info("Data sync complete (%.2fs)", time.time() - start)
    except Exception as e:
        logger.error("Phase 1 failed — proceeding with stale data: %s", e)

    # Phase 2 — analyze (spot then futures sequentially to avoid exchange rate limits)
    logger.info("[PHASE 2] Running spot analysis (4H) ...")
    spot_signal = None
    try:
        spot_signal = analyze_spot_signal(symbol="BTC/USDT", include_news=True)
    except Exception as e:
        logger.error("Spot analysis failed: %s", e)

    logger.info("[PHASE 2] Running futures analysis (1H) ...")
    futures_signal = None
    try:
        futures_signal = analyze_futures_signal(symbol="BTC/USDT", include_news=True)
    except Exception as e:
        logger.error("Futures analysis failed: %s", e)

    # Cross-check: spot vs futures directional conflict
    if spot_signal and futures_signal:
        spot_dir = spot_signal["type"]
        fut_dir = futures_signal["type"]
        if spot_dir != "HOLD" and fut_dir != "HOLD" and spot_dir != fut_dir:
            logger.warning(
                "⚠️  Directional conflict: SPOT %s vs FUTURES %s — skipping both",
                spot_dir, fut_dir,
            )
            spot_signal["type"] = "HOLD"
            spot_signal["reasons"].append(
                f"⚠️  CONFLICT: SPOT {spot_dir} vs FUTURES {fut_dir} — both suppressed"
            )
            futures_signal["type"] = "HOLD"
            futures_signal["reasons"].append(
                f"⚠️  CONFLICT: FUTURES {fut_dir} vs SPOT {spot_dir} — both suppressed"
            )

    # Combined terminal display (once, not per-mode)
    display_combined(spot_signal, futures_signal)

    # Phase 3 — paper trading
    logger.info("[PHASE 3] Updating paper positions ...")
    phase3_actions = []     # track what happened for summary
    pending_tg    = []      # defer Telegram notifications until after Phase 4
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

                # Gate 0c: fakeout check (dangerous for any entry)
                elif fakeout_warn:
                    logger.info("Spot %s — %s — skipping", spot_signal['type'], fakeout_warn)
                    phase3_actions.append(f"⏭ SPOT  {fakeout_warn}")

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
                        # Tighten SL for pyramid entries (after sizing so base_size stays correct)
                        tighten = pyramid_cfg.get("tighten_sl_factor", 0.8)
                        sl_mult = tighten ** (entry_number - 1)  # 1.0 → 0.8 → 0.64
                        if atr > 0 and sl_mult < 1.0:
                            orig_sl = spot_signal["stop_loss"]
                            sl_dist = abs(spot_signal["entry_price"] - orig_sl)
                            new_sl = spot_signal["entry_price"] - sl_dist * sl_mult
                            spot_signal["stop_loss"] = round(new_sl, 2)
                            # Recalculate TP1 with same R:R from tighter SL
                            new_atr_stop = sl_dist * sl_mult
                            spot_signal["take_profit"] = round(spot_signal["entry_price"] + new_atr_stop * RISK_CONFIG["take_profit_rr"], 2)
                            spot_signal["tp2"] = round(spot_signal["entry_price"] + new_atr_stop * RISK_CONFIG["take_profit_rr"] * 2, 2)

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
                            sl_note = f" [SL ×{sl_mult:.2f}]" if sl_mult < 1.0 else ""
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
            opp_dir = "SELL" if futures_signal["type"] == "BUY" else "BUY"
            opp_positions = [p for p in get_open_positions("futures") if p["type"] == opp_dir]
            flipped = False

            for opp in opp_positions:
                entry = opp["entry_price"]
                flip_px = futures_signal["entry_price"]  # close at new signal price
                pnl = ((flip_px - entry) / entry * 100) if opp_dir == "SELL" else ((entry - flip_px) / entry * 100)
                close_paper_position(opp["id"], "FLIP", round(pnl, 2), closed_at=flip_px)
                msg = f"FUT {opp['type']} flipped → {futures_signal['type']} (#{opp['id']} closed, P&L {pnl:+.2f}%)"
                logger.info(msg)
                phase3_actions.append(f"🔄 {msg}")
                flipped = True

            if flipped:
                # After flip, open the new position
                pid = open_paper_position(futures_signal, mode="futures")
                msg = f"FUT {futures_signal['type']} opened (#{pid}) @ ${futures_signal['entry_price']:,.0f}"
                logger.info(msg)
                phase3_actions.append(f"🚀 {msg}")
                pending_tg.append((_format_open_notification(futures_signal, pid, "futures"), "position-open"))
            elif has_open_position_same_direction(futures_signal["type"], "futures"):
                msg = f"Already have open {futures_signal['type']} futures — skipping"
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

        closed_spot = check_and_close_positions(current_price, mode="spot")
        closed_fut   = check_and_close_positions(current_price, mode="futures")
        all_closed   = (closed_spot or []) + (closed_fut or [])

        print_open_status("spot")
        print_open_status("futures")
        print_paper_summary("spot")
        print_paper_summary("futures")

        if phase3_actions:
            print(f"\n  {'─' * 40}")
            print(f"  PHASE 3: POSITIONS")
            print(f"  {'─' * 40}")
            for action in phase3_actions:
                print(f"  {action}")

        # Collect close notification (defer to after Phase 4)
        if all_closed:
            close_msg = _format_close_notification(all_closed)
            if close_msg:
                pending_tg.append((close_msg, "position-close"))

    except Exception as e:
        logger.error("Paper trading update failed: %s", e)

    # Phase 4 — main signal alert FIRST
    send_signal_alert(spot_signal=spot_signal, futures_signal=futures_signal)

    # Then position open/close/warning
    for msg, label in pending_tg:
        _send_telegram_message(msg, label)


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

        closed_spot = check_and_close_positions(price, mode="spot")
        closed_fut = check_and_close_positions(price, mode="futures")
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

        logger.info(
            "Scheduled mode: full cycle at :%02d, position check at :%02d. "
            "Press Ctrl+C to stop.",
            full_minute, check_minute,
        )
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
                logger.info("=== Full cycle starting ===")
                run_cycle()
                next_min = check_minute
                wait_sec = ((next_min - datetime.now().astimezone().minute - 1) % 60) * 60 + (60 - datetime.now().astimezone().second)
            else:
                run_position_check()
                next_min = full_minute
                wait_sec = ((next_min - datetime.now().astimezone().minute - 1) % 60) * 60 + (60 - datetime.now().astimezone().second)
            fired.add(minute)
            logger.info("Next run at :%02d (~%d min)", next_min, max(1, wait_sec // 60))
    else:
        run_cycle()


if __name__ == "__main__":
    main()
