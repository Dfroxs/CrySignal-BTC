"""Telegram notification formatters and senders, in Indonesian.

Messages are read on a phone, every hour. Until 2026-10-09 every hourly card carried
technicals for both modes, HTF, headlines, performance and hypothetical sizing: 3,200 to
3,500 characters whether or not anything happened. That was cut to one line per mode,
which the owner found too terse to follow ("bearish 6.00 · BUY-only"). Since 2026-10-15
each mode gets a short block: what it decided, its score against the bar and the gap,
one sentence saying why, and one line of context. Trade setup, sizing and top reasons
still appear only for a signal that clears the confidence bar (``will_open``).
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
_VETO_CHARS = 60          # the ⛔ reason quoted on a vetoed HOLD, same rule
_BLOCK_CHARS = 90         # a Phase 3 gate's reason, same rule

_CLOSE_LABELS = {
    "TP1":           "TP1 tercapai · stop pindah ke dekat entry",
    "TP2":           "TP2 tercapai · profit penuh",
    "Trail":         "trailing stop",
    "SL":            "stop loss",
    "MACRO_CLOSE":   "ditutup paksa sebelum berita makro",
    "FLIP":          "sinyal berbalik · flip",
    "TIME_EXIT":     "batas waktu tahan habis",
    "VOL_EXIT":      "volatilitas melonjak",
    "FUNDING_EXIT":  "biaya funding terlalu mahal",
    "BREAKER_CLOSE": "circuit breaker · equity",
}

_SIDE_WORD = {"BUY": "beli", "SELL": "jual"}


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
    tps = f"TP1 <code>${tp1:,.0f}</code> (+{tp1_pct:.2f}%)"
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        tps += f" · TP2 <code>${tp2:,.0f}</code> (+{tp2_pct:.2f}%)"
    return [
        f"Entry <code>${entry:,.0f}</code> · R/R <b>1:{rr:.2f}</b>",
        f"SL <code>${sl:,.0f}</code> (-{sl_pct:.2f}%)",
        tps,
    ]


def _openable(sig):
    # Imported here, as before: signals.market_data pulls in the exchange client.
    from signals.market_data import will_open
    return will_open(sig)


def _score_line(sig):
    """Both sides' scores against the bar, and how far the leader is from it."""
    buy_s = sig.get("buy_score", 0) or 0
    sell_s = sig.get("sell_score", 0) or 0
    thr = sig.get("_threshold", 0) or 0
    line = f"Skor beli {buy_s:.2f} · jual {sell_s:.2f} · batas {thr:.2f}"
    lead = max(buy_s, sell_s)
    if sig.get("type", "HOLD") == "HOLD" and buy_s != sell_s and lead < thr:
        line += f" (kurang {thr - lead:.2f})"
    return line


def _why_hold(sig):
    """One plain sentence: why this mode is holding."""
    mode = sig.get("mode", "futures")
    buy_s = sig.get("buy_score", 0) or 0
    sell_s = sig.get("sell_score", 0) or 0
    thr = sig.get("_threshold", 0) or 0
    if buy_s == sell_s:
        return "Tidak ada arah yang jelas."
    if mode == "spot" and sell_s > buy_s:
        return "Pasar condong turun; spot hanya bisa beli, jadi menunggu."
    side, lead = ("BUY", buy_s) if buy_s > sell_s else ("SELL", sell_s)
    if lead >= thr:
        # A veto gate (no-chase, anti-FOMO, entry wick, momentum, counter-trend) turns
        # a scored signal into HOLD and leaves a ⛔ reason. Name it; only without one
        # is the news/macro overlay the likely cause.
        veto = next((str(r) for r in (sig.get("reasons") or []) if "⛔" in str(r)), None)
        if veto:
            text = veto.replace("⛔", "", 1).strip()[:_VETO_CHARS]
            return f"Skor {side} cukup, tapi diveto: {_esc(text)}"
        return f"Skor {side} cukup, tapi ditahan filter berita/makro."
    if mode == "spot":
        return "Belum cukup kuat untuk beli."
    return f"Belum cukup kuat untuk {'long' if side == 'BUY' else 'short'}."


def _verdict_block(sig):
    """The lines for one mode. Returns (lines, actionable).

    A scored BUY/SELL below the confidence bar CANNOT open: Phase 3 refuses it
    unconditionally. It used to be announced as `🟢 BUY · SPOT · WEAK`; 25 of paper
    run 1's 29 futures signals were exactly that, and futures opened nothing in 35 days.
    The row still reaches cycle_log, which is data. What stops is the alert claiming
    something will happen.
    """
    stype = sig.get("type", "HOLD")
    label = _esc(_mode_label(sig))
    conf = _esc(sig.get("confidence") or "")
    conf_s = f" · {conf}" if conf else ""
    score = sig.get("strength", 0) or 0
    thr = sig.get("_threshold", 0) or 0

    if stype in ("BUY", "SELL"):
        sig_line = f"Skor {stype} {score:.2f} · batas {thr:.2f}"
        if not _openable(sig):
            # The bar here is CONFIDENCE, not the score threshold: the score has
            # already cleared it. Name the bar that actually refused it.
            return [f"🔇 <b>{label} — SINYAL {stype}</b>{conf_s}",
                    sig_line,
                    f"→ Skor lolos batas, tapi confidence {conf or '?'} terlalu rendah; "
                    f"posisi tidak dibuka."], False
        block = sig.get("_phase3_block")
        if block:
            # Cleared the confidence bar, then an entry gate refused it. Without this the
            # card showed entry, SL, TP and size for a position that never opened.
            gate, reason = block
            return [f"⛔ <b>{label} — DIBLOKIR</b>{conf_s}",
                    sig_line,
                    f"→ Ditolak gate <b>{_esc(gate)}</b>, posisi tidak dibuka: "
                    f"{_esc(str(reason)[:_BLOCK_CHARS])}"], False
        icon = "🟢" if stype == "BUY" else "🔴"
        return [f"{icon} <b>{label} — SINYAL {stype}</b>{conf_s}",
                sig_line,
                "→ Lolos semua filter."], True

    return [f"⏸ <b>{label} — TAHAN</b>", _score_line(sig), f"→ {_why_hold(sig)}"], False


def _sizing_line(sig):
    mode = sig.get("mode", "futures")
    try:
        if mode == "spot":
            pos = calculate_position_size(sig)
            if not pos.get("usdt_amount"):
                return None
            return (f"Ukuran <code>${pos['usdt_amount']:,.0f}</code> ({pos['position_ratio']:.1f}%) · "
                    f"risiko <code>${pos.get('risk_amount', 0):,.0f}</code>")
        fut = calculate_futures_position(sig)
        if not fut:
            return None
        return (f"{_esc(fut['direction'])} {fut['leverage']}x · margin <code>${fut['margin']:,.0f}</code> · "
                f"likuidasi <code>${fut['liquidation_price']:,.0f}</code> · "
                f"risiko <code>${fut['risk_amount']:,.0f}</code>")
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


def _trend_line(sig):
    """Trend and sentiment for one mode: EMA200 side, RSI, regime, Fear & Greed."""
    last = sig.get("_last") or {}
    parts = []
    price = _price(sig)
    ema200 = last.get("ema200")
    if ema200 and price:
        parts.append(f"di atas EMA200 {_UP}" if price > ema200 else f"di bawah EMA200 {_DOWN}")
    if last.get("rsi") is not None:
        parts.append(f"RSI {last['rsi']:.0f}")
    if sig.get("regime"):
        parts.append(_esc(sig["regime"]))
    if sig.get("fear_greed_value") is not None:
        parts.append(f"F&amp;G {sig['fear_greed_value']}")
    return ("Tren: " + " · ".join(parts)) if parts else None


def _market_line(sig):
    """Futures market structure: funding, L/S, open interest."""
    mkt = sig.get("_market") or {}
    if not mkt:
        return None
    funding = mkt.get("funding", {})
    ls = mkt.get("long_short", {})
    oi = mkt.get("open_interest", {})
    parts = [f"Funding {funding.get('rate_pct', 0):+.4f}%", f"L/S {ls.get('ratio', 1):.2f}"]
    if oi.get("notional", 0) > 1e6:
        parts.append(f"OI ${oi['notional'] / 1e9:.1f}B {_dir(oi.get('change_pct', 0))}")
    return " · ".join(parts)


def _context_line(sig):
    if sig.get("mode") == "spot":
        return _trend_line(sig)
    return _market_line(sig) or _trend_line(sig)


def _open_position_lines(prices=None):
    """Open positions, one line each, with the move since entry at this cycle's price."""
    try:
        positions = _sh.get_open_positions()
    except Exception:  # noqa: BLE001
        return []
    if not positions:
        return ["📂 Posisi terbuka: tidak ada"]
    lines = ["📂 Posisi terbuka:"]
    for p in positions:
        sl = p.get("trailing_stop") or p["stop_loss"]
        # pyramid_entry is 1 for a first entry, so only 2+ is a pyramid level.
        pyr = f" (pyramid {p['pyramid_entry']})" if (p.get("pyramid_entry") or 0) > 1 else ""
        tp1 = " · TP1 ✓" if p.get("partial_closed") else ""
        now = (prices or {}).get(p.get("mode"))
        move = ""
        if now and p.get("entry_price"):
            pct = (now - p["entry_price"]) / p["entry_price"] * 100
            move = f" · {pct if p['type'] == 'BUY' else -pct:+.2f}% sekarang"
        lines.append(f"  {_mode_name(p.get('mode'))} {_esc(p['type'])} #{p['id']}{pyr} @ "
                     f"<code>${p['entry_price']:,.0f}</code> · SL <code>${sl:,.0f}</code>{tp1}{move}")
    return lines


def _render(signals):
    """The one layout both public formatters share."""
    signals = [s for s in signals if s]
    if not signals:
        return ""
    blocks = [(s, *_verdict_block(s)) for s in signals]
    actionable = any(act for _, _, act in blocks)

    primary = signals[-1]                    # futures when present, as before
    price = _price(primary)
    time_str = datetime.now().astimezone().strftime("%H:%M %Z").strip()
    head_icon = "🔔" if actionable else "⏸"
    head = f"{head_icon} <b>BTC ${price:,.0f}</b>" if price else f"{head_icon} <b>BTC</b>"
    lines = [f"{head} · {_esc(time_str)}",
             "Ada sinyal yang lolos jam ini." if actionable else "Tidak ada posisi baru jam ini."]

    for sig, block, act in blocks:
        lines.append("")
        lines += block
        if act:
            lines += _detail_lines(sig)
        ctx = _context_line(sig)
        if ctx:
            lines.append(ctx)

    prices = {s.get("mode", "futures"): _price(s) for s in signals}
    pos = _open_position_lines(prices)
    if pos:
        lines.append("")
        lines += pos
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def _format_compact_signal_telegram(signal):
    """Single-mode card — same layout as the combined one."""
    return _render([signal])


def _format_consolidated_telegram(spot_signal, futures_signal):
    """Combined hourly card: price, one block per mode, detail only when actionable."""
    return _render([spot_signal, futures_signal])


def _format_close_notification(closed):
    """Position closes (TP/SL/MACRO…): outcome and P&L per trade, then the running
    total for each mode that closed — performance only changes here, so it lives here
    rather than in every hourly card."""
    if not closed:
        return None

    # A TP1 hit closes half and leaves the position open: "Position closed" over it
    # told the owner the trade was over.
    partial_only = all(c.get("outcome") == "TP1" for c in closed)
    lines = (["🔔 <b>Take-profit sebagian (TP1)</b>", "Setengah posisi ditutup, sisanya tetap berjalan."]
             if partial_only else ["🔔 <b>Posisi ditutup</b>"])
    modes = []
    for c in closed:
        # Colour by result, not side: "🔴 FUTURES SELL +3.10%" read as a loss.
        icon = "🟢" if c["pnl"] >= 0 else "🔴"
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
            totals.append(f"{_mode_name(mode)} <b>{pnl:+.2f}%</b> ({cnt} trade)")
    if totals:
        lines.append("Total semua trade: " + " · ".join(totals))
    return "\n".join(lines)


def _format_open_notification(signal, pos_id, mode, pyramid_entry=None):
    """'Position opened' card — sent separately from the hourly one."""
    stype = signal["type"]
    icon = "🟢" if stype == "BUY" else "🔴"
    what = f"{_mode_name(mode)} {_esc(stype)}"
    if pyramid_entry:
        header = f"🧩 {icon} <b>Pyramid #{pyramid_entry} · {what}</b> · posisi #{pos_id}"
    else:
        header = f"🚀 {icon} <b>Posisi dibuka · {what}</b> · #{pos_id}"
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

