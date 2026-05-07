"""Shared helpers and public API dispatcher for Telegram + Discord notifications."""

import logging
from datetime import datetime

from trading import history as _sh
from config import (
    DISCORD_WEBHOOK_URL,
    HTTP_SESSION,
    SIGNAL_MAX_SCORE,
    SPOT_MAX_SCORE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)

_UP = "▲"
_DOWN = "▼"
_FLAT = "─"


def _esc(text):
    """Escape HTML special characters for Telegram HTML parse mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dir(val):
    if val > 0:
        return _UP
    if val < 0:
        return _DOWN
    return _FLAT


def _max_score(signal):
    return SPOT_MAX_SCORE if signal.get("mode") == "spot" else SIGNAL_MAX_SCORE


def _mode_label(signal):
    return "SPOT 4H" if signal.get("mode") == "spot" else "FUTURES 1H"


def _macro_banner(signal):
    """Return a warning banner if the signal was penalized for a macro event."""
    for r in signal.get("reasons", []):
        if "MACRO CAUTION" in r:
            return "⚠️ <b>MACRO RISK: High-impact event approaching — position may be force-closed</b>"
    return None


def _send_telegram_message(text, label="alert"):
    """Send a single Telegram HTML message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("Telegram %s sent.", label)
            return True
        else:
            logger.warning("Telegram %s returned %d: %s", label, resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("Telegram %s failed: %s", label, e)
        return False


def send_signal_alert(spot_signal=None, futures_signal=None, symbol="BTC/USDT"):
    """Send combined SPOT + FUTURES alert to all configured channels."""
    from notifier.telegram import _format_compact_signal_telegram, _send_combined_telegram
    from notifier.discord import _send_combined_discord, send_discord_alert

    # Backward compat: positional single-signal call
    if futures_signal is None and spot_signal is not None and isinstance(spot_signal, dict):
        if "mode" not in spot_signal:
            single = spot_signal
            text = _format_compact_signal_telegram(single)
            _send_telegram_message(text, "signal")
            send_discord_alert(single, symbol)
            return

    has_spot = spot_signal is not None
    has_futures = futures_signal is not None
    spot_active = has_spot and spot_signal is not None
    futures_active = has_futures and futures_signal is not None

    if not spot_active and not futures_active:
        return

    if has_spot and has_futures:
        _send_combined_telegram(spot_signal, futures_signal, symbol)
        _send_combined_discord(spot_signal, futures_signal, symbol)
    elif spot_active:
        _send_telegram_message(_format_compact_signal_telegram(spot_signal), "spot-signal")
        send_discord_alert(spot_signal, symbol)
    elif futures_active:
        _send_telegram_message(_format_compact_signal_telegram(futures_signal), "futures-signal")
        send_discord_alert(futures_signal, symbol)
