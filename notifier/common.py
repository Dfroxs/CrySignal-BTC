"""Shared helpers and public API dispatcher for Telegram notifications."""

import logging
from datetime import datetime

from config import (
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
    """Send combined SPOT + FUTURES alert to Telegram."""
    from notifier.telegram import _format_compact_signal_telegram, _send_combined_telegram

    # Backward compat: positional single-signal call
    if futures_signal is None and spot_signal is not None and isinstance(spot_signal, dict):
        if "mode" not in spot_signal:
            text = _format_compact_signal_telegram(spot_signal)
            _send_telegram_message(text, "signal")
            return

    has_spot = spot_signal is not None
    has_futures = futures_signal is not None

    if not has_spot and not has_futures:
        return

    if has_spot and has_futures:
        _send_combined_telegram(spot_signal, futures_signal, symbol)
    elif has_spot:
        _send_telegram_message(_format_compact_signal_telegram(spot_signal), "spot-signal")
    else:
        _send_telegram_message(_format_compact_signal_telegram(futures_signal), "futures-signal")
