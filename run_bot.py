#!/usr/bin/env python3
"""SpotSignal — BTC/USDT trading signal bot.

Two-phase pipeline: scrape news → analyze → paper trade → notify.
"""

import argparse
import atexit
import logging
import sys
import time
from datetime import UTC, datetime

from core_analysis import analyze_btc_signal
from news_scraper import scrape_and_export
from notifier import send_signal_alert
from paper_trader import check_and_close_positions, print_open_status, print_paper_summary
from signal_history import close as close_db, open_paper_position

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("spotsignal.log"),
    ],
)
logger = logging.getLogger(__name__)

# Clean shutdown
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

    # Phase 2 — analyze
    logger.info("[PHASE 2] Running core analysis ...")
    signal = None
    try:
        signal = analyze_btc_signal(
            symbol="BTC/USDT", timeframe="1h", include_news=True,
        )
    except Exception as e:
        logger.error("Phase 2 failed: %s", e)

    # Phase 3 — paper trading
    logger.info("[PHASE 3] Updating paper positions ...")
    try:
        if signal and signal["type"] != "HOLD":
            open_paper_position(signal)

        if signal:
            entry = signal.get("entry_price", 0)
        else:
            # Fallback: fetch current price if signal is None
            from core_analysis import exchange
            ticker = exchange.fetch_ticker("BTC/USDT")
            entry = ticker["last"]

        check_and_close_positions(entry)
        print_open_status()
        print_paper_summary()
    except Exception as e:
        logger.error("Paper trading update failed: %s", e)

    # Phase 4 — notify
    if signal:
        send_signal_alert(signal)


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
