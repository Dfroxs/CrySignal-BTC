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
from news_scraper import scrape_and_export
from notifier import _format_close_notification, _send_telegram_message, send_signal_alert
from trading.paper import check_and_close_positions, print_open_status, print_paper_summary
from config import RISK_CONFIG, FUTURES_CONFIG
from trading.history import close as close_db, close_paper_position, get_open_positions, has_open_position_same_direction, open_paper_position

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

    # Phase 3 — paper trading
    logger.info("[PHASE 3] Updating paper positions ...")
    phase3_actions = []  # track what happened for summary
    try:
        # Spot positions — BUY-only (no short selling on spot)
        if spot_signal and spot_signal["type"] != "HOLD":
            spot_open = len(get_open_positions("spot"))
            if spot_open >= RISK_CONFIG["max_positions"]:
                msg = f"Spot max {RISK_CONFIG['max_positions']} positions — skipping {spot_signal['type']}"
                logger.info(msg)
                phase3_actions.append(f"⏭ SPOT: {msg}")
            elif has_open_position_same_direction(spot_signal["type"], "spot"):
                msg = f"Already have open {spot_signal['type']} spot — skipping"
                logger.info(msg)
                phase3_actions.append(f"⏭ SPOT: {msg}")
            else:
                pid = open_paper_position(spot_signal, mode="spot")
                msg = f"SPOT {spot_signal['type']} opened (#{pid}) @ ${spot_signal['entry_price']:,.0f}"
                logger.info(msg)
                phase3_actions.append(f"✅ {msg}")

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
                phase3_actions.append(f"✅ {msg}")
            elif has_open_position_same_direction(futures_signal["type"], "futures"):
                msg = f"Already have open {futures_signal['type']} futures — skipping"
                logger.info(msg)
                phase3_actions.append(f"⏭ FUT: {msg}")
            else:
                pid = open_paper_position(futures_signal, mode="futures")
                msg = f"FUT {futures_signal['type']} opened (#{pid}) @ ${futures_signal['entry_price']:,.0f}"
                logger.info(msg)
                phase3_actions.append(f"✅ {msg}")

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
            print(f"\n📋 Phase 3 summary:")
            for action in phase3_actions:
                print(f"   {action}")

        # Send close notification if any positions closed this cycle
        if all_closed:
            close_msg = _format_close_notification(all_closed)
            if close_msg:
                _send_telegram_message(close_msg, "position-close")

    except Exception as e:
        logger.error("Paper trading update failed: %s", e)

    # Phase 4 — notify
    send_signal_alert(spot_signal=spot_signal, futures_signal=futures_signal)


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
        logger.info(
            "Loop mode: full analysis every %d min, position check at half (%d min). "
            "Press Ctrl+C to stop.",
            args.loop, args.loop // 2,
        )
        half = (args.loop * 60) // 2
        while True:
            run_cycle()
            logger.info("Sleeping %d min until position check ...", args.loop // 2)
            time.sleep(half)
            run_position_check()
            logger.info("Sleeping %d min until next full cycle ...", args.loop // 2)
            time.sleep(half)
    else:
        run_cycle()


if __name__ == "__main__":
    main()
