"""Telegram notification formatters and senders.

Messages are read on a phone, every hour. A cycle where nothing can open is the price
and one line per mode; trade setup, sizing, reasons and market context appear only
for a signal that clears the confidence bar (``will_open``). Until 2026-10-09 every
hourly card carried technicals for both modes, HTF, headlines, performance and
hypothetical sizing — 3,200 to 3,500 characters whether or not anything happened.
"""

import logging
from datetime import datetime

from trading import history as _sh
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from signals.sizing import calculate_futures_position, calculate_position_size
from notifier.common import _dir, _esc, _mode_label, _macro_banner, _send_telegram_message

logger = logging.getLogger(__name__)
_UP = "▲"
_DOWN = "▼"

_TOP_REASONS = 3          # reasons shown under an actionable signal
_REASON_CHARS = 70        # truncated BEFORE escaping, so no entity is cut in half
_VETO_CHARS = 45          # the ⛔ reason quoted on a vetoed HOLD line, same rule

_CLOSE_LABELS = {
    "TP1":           "TP1 hit · trailing→BE",
    "TP2":           "TP2 hit · full win",
    "Trail":         "trailing stop",
    "SL":            "stop loss",
    "MACRO_CLOSE":   "macro force-close",
    "FLIP":          "signal reversed · flip",
    "TIME_EXIT":     "max hold time",
    "VOL_EXIT":      "volatility expansion",
    "FUNDING_EXIT":  "funding cost exit",
    "BREAKER_CLOSE": "circuit breaker · equity",
}


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def _mode_name(mode):
    return "SPOT" if mode == "spot" else "FUTURES"


def _price(sig):
    if not sig:
        return 0
    return sig.get("entry_price") or (sig.get("_last") or {}).get("close", 0)


def _setup_lines(entry, sl, tp1, tp2=None):
    """Entry / SL / TP1 / TP2 / R/R in three lines. SL always renders as a loss and
    TP as a gain, whichever side the trade is on."""
    sl_pct = abs(entry - sl) / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr = tp1_pct / sl_pct if sl_pct > 0 else 0
    tps = f"TP1 <code>${tp1:,.0f}</code> +{tp1_pct:.2f}%"
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        tps += f" · TP2 <code>${tp2:,.0f}</code> +{tp2_pct:.2f}%"
    return [
        f"Entry <code>${entry:,.0f}</code> · R/R <b>1:{rr:.2f}</b>",
        f"SL <code>${sl:,.0f}</code> -{sl_pct:.2f}%",
        tps,
    ]


def _openable(sig):
    # Imported here, as before: signals.market_data pulls in the exchange client.
    from signals.market_data import will_open
    return will_open(sig)


def _verdict_line(sig):
    """One line per mode. Returns (line, actionable).

    A scored BUY/SELL below the confidence bar CANNOT open — Phase 3 refuses it
    unconditionally. It used to be announced as `🟢 BUY · SPOT · WEAK` (and in the
    hourly card as `ENTER BUY`); 25 of paper run 1's 29 futures signals were exactly
    that, and futures opened nothing in 35 days. The row still reaches cycle_log —
    that is data. What stops is the alert claiming something will happen.
    """
    stype = sig.get("type", "HOLD")
    mode = sig.get("mode", "futures")
    label = f"<b>{_esc(_mode_label(sig))}</b>"
    conf = _esc(sig.get("confidence") or "")
    score = sig.get("strength", 0) or 0
    thr = sig.get("_threshold", 0) or 0

    if stype in ("BUY", "SELL"):
        if not _openable(sig):
            conf_s = f" {conf}" if conf else ""
            return (f"🔇 {label} · {stype}{conf_s} {score:.2f} — "
                    f"below the bar, no position will open", False)
        icon = "🟢" if stype == "BUY" else "🔴"
        conf_s = f" {conf}" if conf else ""
        return f"{icon} {label} · <b>{stype}</b>{conf_s} {score:.2f} · bar {thr:.2f}", True

    buy_s = sig.get("buy_score", 0) or 0
    sell_s = sig.get("sell_score", 0) or 0
    if buy_s == sell_s:
        why = "no direction"
    elif mode == "spot" and sell_s > buy_s:
        why = f"bearish {sell_s:.2f} · BUY-only"
    else:
        side, lead = ("BUY", buy_s) if buy_s > sell_s else ("SELL", sell_s)
        if lead >= thr:
            # A veto gate (no-chase, anti-FOMO, entry wick, momentum, counter-trend)
            # turns a scored signal into HOLD and leaves a ⛔ reason. Name it; only
            # without one is the news/macro overlay the likely cause.
            veto = next((str(r) for r in (sig.get("reasons") or []) if "⛔" in str(r)), None)
            if veto:
                text = veto.replace("⛔", "", 1).strip()[:_VETO_CHARS]
                why = f"{side} {lead:.2f} ≥ bar {thr:.2f} · vetoed: {_esc(text)}"
            else:
                why = f"{side} {lead:.2f} ≥ bar {thr:.2f} · held back (news/macro)"
        else:
            why = f"{side} {lead:.2f} · bar {thr:.2f}"
    return f"⏸ {label} · HOLD · {why}", False


def _sizing_line(sig):
    mode = sig.get("mode", "futures")
    try:
        if mode == "spot":
            pos = calculate_position_size(sig)
            if not pos.get("usdt_amount"):
                return None
            return (f"Size <code>${pos['usdt_amount']:,.0f}</code> ({pos['position_ratio']:.1f}%) · "
                    f"risk <code>${pos.get('risk_amount', 0):,.0f}</code>")
        fut = calculate_futures_position(sig)
        if not fut:
            return None
        return (f"{_esc(fut['direction'])} {fut['leverage']}x · margin <code>${fut['margin']:,.0f}</code> · "
                f"Liq <code>${fut['liquidation_price']:,.0f}</code> · risk <code>${fut['risk_amount']:,.0f}</code>")
    except Exception as e:  # noqa: BLE001 — sizing must never cost the alert
        logger.debug("sizing line skipped: %s", e)
        return None


def _detail_lines(sig):
    """Setup, sizing and top reasons — only for a signal that can open.

    Futures levels are shown as the position OPENS them: stop and targets widened by
    `apply_futures_exit_geometry`, the same call run_bot makes after the gates. Sizing,
    liquidation and risk then follow from the stop actually used."""
    if sig.get("mode") == "futures":
        from trading.paper import apply_futures_exit_geometry
        sig = apply_futures_exit_geometry(sig)
    lines = []
    if sig.get("stop_loss") and sig.get("take_profit") and sig.get("entry_price"):
        lines += _setup_lines(sig["entry_price"], sig["stop_loss"], sig["take_profit"], sig.get("tp2"))
    sizing = _sizing_line(sig)
    if sizing:
        lines.append(sizing)
    for r in (sig.get("reasons") or [])[:_TOP_REASONS]:
        lines.append(_esc(str(r).strip()[:_REASON_CHARS]))
    return lines


def _context_lines(sig, futures_sig=None):
    """Trend + sentiment in one line, futures market structure in a second."""
    lines = []
    last = sig.get("_last") or {}
    parts = []
    price = _price(sig)
    ema200 = last.get("ema200")
    if ema200:
        parts.append(f"EMA200 {_UP if price > ema200 else _DOWN}")
    if last.get("rsi") is not None:
        parts.append(f"RSI {last['rsi']:.0f}")
    if sig.get("regime"):
        parts.append(_esc(sig["regime"]))
    if sig.get("fear_greed_value") is not None:
        parts.append(f"F&amp;G {sig['fear_greed_value']} {_esc(sig.get('fear_greed_label', ''))}".rstrip())
    if parts:
        lines.append(" · ".join(parts))

    mkt = (futures_sig or {}).get("_market") or {}
    if futures_sig and mkt:
        funding = mkt.get("funding", {})
        ls = mkt.get("long_short", {})
        oi = mkt.get("open_interest", {})
        fparts = [f"Funding {funding.get('rate_pct', 0):+.4f}%", f"L/S {ls.get('ratio', 1):.2f}"]
        if oi.get("notional", 0) > 1e6:
            fparts.append(f"OI ${oi['notional'] / 1e9:.1f}B {_dir(oi.get('change_pct', 0))}")
        lines.append(" · ".join(fparts))
    return lines


def _open_position_lines():
    """One line per open position; nothing at all when there are none."""
    try:
        positions = _sh.get_open_positions()
    except Exception:  # noqa: BLE001
        return []
    lines = []
    for p in positions or []:
        sl = p.get("trailing_stop") or p["stop_loss"]
        pyr = f" pyr{p['pyramid_entry']}" if p.get("pyramid_entry") else ""
        tp1 = " · TP1 ✓" if p.get("partial_closed") else ""
        lines.append(f"📂 {_mode_name(p.get('mode'))} {_esc(p['type'])} #{p['id']}{pyr} @ "
                     f"<code>${p['entry_price']:,.0f}</code> · SL <code>${sl:,.0f}</code>{tp1}")
    return lines


def _render(signals):
    """The one layout both public formatters share."""
    signals = [s for s in signals if s]
    if not signals:
        return ""
    verdicts = [(s, *_verdict_line(s)) for s in signals]
    actionable = [s for s, _, act in verdicts if act]

    primary = signals[-1]                    # futures when present, as before
    price = _price(primary)
    time_str = datetime.now().astimezone().strftime("%H:%M %Z").strip()
    head_icon = "🔔" if actionable else "⏸"
    head = f"{head_icon} <b>BTC ${price:,.0f}</b>" if price else f"{head_icon} <b>BTC</b>"
    lines = [f"{head} · {_esc(time_str)}"]

    for i, (sig, line, act) in enumerate(verdicts):
        if act:
            # A setup block gets a blank line on each side so two of them, or one
            # and a HOLD line, do not run together on a phone screen.
            if i > 0:
                lines.append("")
            lines.append(line)
            lines += _detail_lines(sig)
            if i < len(verdicts) - 1:
                lines.append("")
        else:
            lines.append(line)

    lines += _open_position_lines()

    if actionable:
        fut = next((s for s in actionable if s.get("mode", "futures") != "spot"), None)
        ctx = _context_lines(actionable[0], fut)
        if ctx:
            lines.append("")
            lines += ctx
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def _format_compact_signal_telegram(signal):
    """Single-mode card — same layout as the combined one."""
    return _render([signal])


def _format_consolidated_telegram(spot_signal, futures_signal):
    """Combined hourly card: price, one line per mode, detail only when actionable."""
    return _render([spot_signal, futures_signal])


def _format_close_notification(closed):
    """Position closes (TP/SL/MACRO…): outcome and P&L per trade, then the running
    total for each mode that closed — performance only changes here, so it lives here
    rather than in every hourly card."""
    if not closed:
        return None

    lines = ["🔔 <b>Position closed</b>"]
    modes = []
    for c in closed:
        icon = "🟢" if c["type"] == "BUY" else "🔴"
        mode = c.get("mode", "futures")
        if mode not in modes:
            modes.append(mode)
        pnl = c["pnl"]
        label = _esc(_CLOSE_LABELS.get(c["outcome"], c["outcome"]))
        lines.append(f"{icon} {_mode_name(mode)} {_esc(c['type'])} <b>{pnl:+.2f}%</b> · {label}")
        lines.append(f"   <code>${c['entry']:,.0f}</code> → <code>${c['exit']:,.0f}</code>")

    totals = []
    for mode in modes:
        try:
            pnl, cnt, _ = _sh.get_closed_pnl(mode)
        except Exception:  # noqa: BLE001 — a DB failure drops the total, never the close
            continue
        if cnt:
            totals.append(f"{_mode_name(mode)} <b>{pnl:+.2f}%</b> ({cnt} trade{'' if cnt == 1 else 's'})")
    if totals:
        lines.append("Total: " + " · ".join(totals))
    return "\n".join(lines)


def _format_open_notification(signal, pos_id, mode, pyramid_entry=None):
    """'Position opened' card — sent separately from the hourly one."""
    stype = signal["type"]
    icon = "🟢" if stype == "BUY" else "🔴"
    what = f"{_mode_name(mode)} {_esc(stype)}"
    if pyramid_entry:
        header = f"🧩 {icon} <b>Pyramid #{pyramid_entry} · {what}</b> · pos #{pos_id}"
    else:
        header = f"🚀 {icon} <b>Opened · {what}</b> · #{pos_id}"
    return "\n".join([header] + _setup_lines(signal["entry_price"], signal["stop_loss"],
                                             signal["take_profit"], signal.get("tp2")))


def _send_combined_telegram(spot_signal, futures_signal, symbol):
    """Send single consolidated message + optional macro banner.

    Returns the number of messages actually delivered.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    sent = 0

    # Macro risk banner (separate — needs to be prominent)
    macro_warn = _macro_banner(spot_signal) or _macro_banner(futures_signal)
    if macro_warn:
        if _send_telegram_message(macro_warn, "macro-warning"):
            sent += 1

    # Main consolidated message
    text = _format_consolidated_telegram(spot_signal, futures_signal)
    if _send_telegram_message(text, "combined"):
        sent += 1

    if sent:
        logger.info("Telegram: %d message(s) delivered", sent)
    return sent

