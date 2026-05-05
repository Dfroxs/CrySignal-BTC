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

from core_analysis import analyze_spot_signal, analyze_futures_signal
from news_scraper import scrape_and_export
from notifier import _format_close_notification, _send_telegram_message, send_signal_alert
from paper_trader import check_and_close_positions, print_open_status, print_paper_summary
from config import RISK_CONFIG, FUTURES_CONFIG
from signal_history import close as close_db, get_open_positions, has_open_position_same_direction, open_paper_position

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
        # Spot positions
        if spot_signal and spot_signal["type"] != "HOLD":
            spot_open = len(get_open_positions("spot"))
            if spot_open >= RISK_CONFIG["max_positions"]:
                msg = f"Max spot ({RISK_CONFIG['max_positions']}) — skipping {spot_signal['type']}"
                logger.info(msg)
                phase3_actions.append(f"⏭ SPOT: {msg}")
            elif has_open_position_same_direction(spot_signal["type"], "spot"):
                msg = f"Duplicate {spot_signal['type']} direction — skipping spot"
                logger.info(msg)
                phase3_actions.append(f"⏭ SPOT: {msg}")
            else:
                pid = open_paper_position(spot_signal, mode="spot")
                msg = f"SPOT {spot_signal['type']} opened (#{pid}) @ ${spot_signal['entry_price']:,.0f}"
                logger.info(msg)
                phase3_actions.append(f"✅ {msg}")

        # Futures positions
        if futures_signal and futures_signal["type"] != "HOLD":
            fut_open = len(get_open_positions("futures"))
            if fut_open >= FUTURES_CONFIG["max_positions"]:
                msg = f"Max futures ({FUTURES_CONFIG['max_positions']}) — skipping {futures_signal['type']}"
                logger.info(msg)
                phase3_actions.append(f"⏭ FUT: {msg}")
            elif has_open_position_same_direction(futures_signal["type"], "futures"):
                msg = f"Duplicate {futures_signal['type']} direction — skipping futures"
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
            from core_analysis import exchange
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
            "Loop mode: running every %d minutes. Press Ctrl+C to stop.",
            args.loop,
        )
        while True:
            run_cycle()
            logger.info("Sleeping %d minutes until next cycle ...", args.loop)
            time.sleep(args.loop * 60)
    else:
        run_cycle()


if __name__ == "__main__":
    main()
