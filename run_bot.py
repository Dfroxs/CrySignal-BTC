#!/usr/bin/env python3
import argparse
import logging
import sys
import time
from datetime import datetime

from core_analysis import analyze_btc_signal
from news_scraper import scrape_and_export
from notifier import send_signal_alert

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('spotsignal.log'),
    ],
)
logger = logging.getLogger(__name__)


def print_header():
    print("\n" + "=" * 70)
    print("🚀 SPOT SIGNAL TRADING BOT — PRODUCTION EDITION".center(70))
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(70))
    print("=" * 70)


def run_cycle():
    print_header()

    logger.info("[PHASE 1] Updating market intelligence...")
    start = time.time()
    try:
        scrape_and_export()
        logger.info(f"Data sync complete ({time.time() - start:.2f}s)")
    except Exception as e:
        # Non-fatal: analysis continues on stale CSV data
        logger.error(f"Phase 1 failed — proceeding with stale data: {e}")

    logger.info("[PHASE 2] Running core analysis...")
    try:
        signal = analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True)
        if signal:
            send_signal_alert(signal)
    except Exception as e:
        logger.error(f"Phase 2 failed: {e}")

    print("\n" + "=" * 70)
    print("✅ CYCLE COMPLETE".center(70))
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description='SpotSignal BTC Trading Bot')
    parser.add_argument(
        '--loop', type=int, default=0, metavar='MINUTES',
        help='Run continuously every N minutes (omit to run once)',
    )
    args = parser.parse_args()

    if args.loop > 0:
        logger.info(f"Loop mode: running every {args.loop} minutes. Press Ctrl+C to stop.")
        while True:
            run_cycle()
            logger.info(f"Sleeping {args.loop} minutes until next cycle...")
            time.sleep(args.loop * 60)
    else:
        run_cycle()


if __name__ == '__main__':
    main()
