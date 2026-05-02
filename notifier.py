import logging

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SIGNAL_MAX_SCORE

logger = logging.getLogger(__name__)


def send_signal_alert(signal, symbol='BTC/USDT'):
    """Send a Telegram message for BUY/SELL signals. No-op if token not configured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if signal['type'] == 'HOLD':
        return

    icon = '🟢' if signal['type'] == 'BUY' else '🔴'
    entry = signal['entry_price']
    sl    = signal['stop_loss']
    tp    = signal['take_profit']
    sl_pct = abs(entry - sl) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    rr     = tp_pct / sl_pct if sl_pct > 0 else 0

    text = (
        f"{icon} *{signal['type']} {symbol}*\n"
        f"Entry:       `${entry:,.2f}`\n"
        f"Stop Loss:   `${sl:,.2f}` (-{sl_pct:.2f}%)\n"
        f"Take Profit: `${tp:,.2f}` (+{tp_pct:.2f}%)\n"
        f"R/R:         `1:{rr:.2f}`\n"
        f"Strength:    `{signal['strength']:.2f} / {SIGNAL_MAX_SCORE}`\n"
        f"F&G:         `{signal.get('fear_greed_value', 'N/A')} — {signal.get('fear_greed_label', '')}`"
    )

    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("Telegram alert sent.")
        else:
            logger.warning(f"Telegram returned {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
