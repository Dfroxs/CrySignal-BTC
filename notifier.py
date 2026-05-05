"""Telegram and Discord alert delivery for BUY / SELL signals."""

import logging

from config import (
    DISCORD_WEBHOOK_URL,
    HTTP_SESSION,
    SIGNAL_MAX_SCORE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _format_signal_message(signal, symbol="BTC/USDT"):
    """Build a plain-text alert string from a signal dict."""
    icon  = "\U0001f7e2" if signal["type"] == "BUY" else "\U0001f534"
    entry = signal["entry_price"]
    sl    = signal["stop_loss"]
    tp1   = signal["take_profit"]
    tp2   = signal.get("tp2")
    atr   = signal.get("atr", 0)

    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    trail_init = abs(atr * 1.0)   # trailing_atr_factor = 1.0
    trail_pct  = trail_init / entry * 100 if entry else 0

    lines = [
        f"{icon} *{signal['type']} {symbol}*",
        f"Entry:         `${entry:,.2f}`",
        f"Trail SL:      `${sl:,.2f}` (-{sl_pct:.2f}%)",
        f"TP1 (50%):     `${tp1:,.2f}` (+{tp1_pct:.2f}%)",
    ]
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        lines.append(f"TP2 (50%):     `${tp2:,.2f}` (+{tp2_pct:.2f}%)")

    lines += [
        f"R/R:           `1:{rr:.2f}`",
        f"Strength:      `{signal['strength']:.2f} / {SIGNAL_MAX_SCORE}`",
        f"F&G:           `{signal.get('fear_greed_value', 'N/A')}"
        f" — {signal.get('fear_greed_label', '')}`",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_telegram_alert(signal, symbol="BTC/USDT"):
    """Send a Telegram message for BUY/SELL signals.

    No-op if ``TELEGRAM_BOT_TOKEN`` or ``TELEGRAM_CHAT_ID`` are unset.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if signal["type"] == "HOLD":
        return

    text = _format_signal_message(signal, symbol)
    try:
        resp = HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown"},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("Telegram alert sent.")
        else:
            logger.warning("Telegram returned %d: %s",
                           resp.status_code, resp.text[:100])
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)


def send_discord_alert(signal, symbol="BTC/USDT"):
    """Send a Discord webhook message for BUY/SELL signals.

    No-op if ``DISCORD_WEBHOOK_URL`` is unset.
    """
    if not DISCORD_WEBHOOK_URL:
        return
    if signal["type"] == "HOLD":
        return

    icon  = "\U0001f7e2" if signal["type"] == "BUY" else "\U0001f534"
    entry = signal["entry_price"]
    sl    = signal["stop_loss"]
    tp1   = signal["take_profit"]
    tp2   = signal.get("tp2")
    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    tp2_line = ""
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        tp2_line = f"\nTP2 (50%): `{tp2:,.2f}` (+{tp2_pct:.2f}%)"

    text = (
        f"{icon} **{signal['type']} {symbol}**\n"
        f"Entry:     `{entry:,.2f}`\n"
        f"Trail SL:  `{sl:,.2f}` (-{sl_pct:.2f}%)\n"
        f"TP1 (50%): `{tp1:,.2f}` (+{tp1_pct:.2f}%)"
        f"{tp2_line}\n"
        f"R/R:       `1:{rr:.2f}`\n"
        f"Strength:  `{signal['strength']:.2f}/{SIGNAL_MAX_SCORE}`"
    )

    try:
        resp = HTTP_SESSION.post(
            DISCORD_WEBHOOK_URL,
            json={"content": text},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            logger.info("Discord alert sent.")
        else:
            logger.warning("Discord returned %d: %s",
                           resp.status_code, resp.text[:100])
    except Exception as e:
        logger.warning("Discord alert failed: %s", e)


def send_signal_alert(signal, symbol="BTC/USDT"):
    """Convenience: send to all configured notification channels."""
    send_telegram_alert(signal, symbol)
    send_discord_alert(signal, symbol)
