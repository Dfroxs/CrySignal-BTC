"""Telegram notification formatters and senders, in Indonesian.

Messages are read on a phone, every hour. Redesigned 2026-11-18 with the owner (mockup
approved): Indonesian numbers ($63.912, 7,0), UTC time, futures before spot, one block per
mode saying what it did and why in plain words (veto and gate reasons translated), levels
only for a signal that clears the confidence bar (``will_open``), then the open BTC
positions and the BTC bot's $1,000 demo account as the dashboard counts it.
"""

import logging
from datetime import UTC, datetime

from trading import history as _sh
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from notifier.common import _esc, _idn, _macro_banner, _send_telegram_message, _usd

logger = logging.getLogger(__name__)
_VETO_CHARS = 60          # the ⛔ reason quoted on a vetoed HOLD, same rule
_BLOCK_CHARS = 90         # a Phase 3 gate's reason, same rule

_CLOSE_LABELS = {
    "TP1":           "kena target 1 (setengah posisi)",
    "TP2":           "kena target 2",
    "Trail":         "kena trailing stop",
    "SL":            "kena stop",
    "MACRO_CLOSE":   "ditutup sebelum berita ekonomi",
    "FLIP":          "ditutup, sinyal berbalik",
    "TIME_EXIT":     "batas waktu tahan habis",
    "VOL_EXIT":      "pasar terlalu liar",
    "FUNDING_EXIT":  "biaya funding terlalu mahal",
    "BREAKER_CLOSE": "pengaman akun",
}

# The engine's veto reasons are technical English; the card says what they mean.
_VETO_PLAIN = (
    ("Counter-trend block: 1D BULLISH", "tren harian masih naik, SELL melawan tren"),
    ("Counter-trend block: 1D BEARISH", "tren harian masih turun, BUY melawan tren"),
    ("No-chase", "harga sudah jauh dari rata-rata (VWAP), mengejar"),
    ("Anti-FOMO", "candle sebelumnya melonjak, mengejar lonjakan"),
    ("Entry wick", "candle terakhir berekor panjang, ditolak pasar"),
    ("Short-term", "momentum jangka pendek berlawanan"),
    ("R:R", "rasio untung/rugi di bawah 1,5"),
)
# Phase 3 entry gates (run_bot `_block`), in plain words.
_GATE_PLAIN = {
    "reentry_first": "harga masuk lebih buruk dari posisi terakhir",
    "confidence_first": "keyakinan terlalu rendah",
    "fakeout_first": "pola breakout palsu",
    "psy_sl_first": "stop terlalu dekat angka bulat",
    "sr_first": "terlalu dekat support/resistance",
    "regime_counter": "melawan arah tren",
    "regime_bearish": "pasar sedang turun",
    "trend_confluence": "konfirmasi tren kurang",
    "breakout_chase": "mengejar breakout",
    "aggregate_risk": "risiko total terlalu besar",
    "already_open": "posisi searah sudah terbuka",
    "stale_cache": "data harga belum diperbarui",
}


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def _mode_name(mode):
    return "spot" if mode == "spot" else "futures"


def _side(stype):
    return "LONG" if stype == "BUY" else "SHORT"


def _price(sig):
    if not sig:
        return 0
    return sig.get("entry_price") or (sig.get("_last") or {}).get("close", 0)


def _levels_lines(stype, entry, sl, tp1, tp2=None):
    """Entry, stop and targets with their distance, a loss as −, a gain as +."""
    pct = lambda x: abs(x - entry) / entry * 100
    lines = [f"Masuk {_usd(entry, 0)} · stop {_usd(sl, 0)} (−{_idn(pct(sl), 1)}%)",
             f"Target 1 {_usd(tp1, 0)} (+{_idn(pct(tp1), 1)}%)"]
    if tp2:
        lines.append(f"Target 2 {_usd(tp2, 0)} (+{_idn(pct(tp2), 1)}%)")
    return lines


def _sizing_lines(sig):
    """Leverage, margin, liquidation and the loss at the stop, as the sizing module would
    open it. Margin and risk in % of the account: the sizing balances are examples, and the
    owner's demo accounts are $1,000. Display only; the paper book records unlevered moves."""
    try:
        from signals.sizing import calculate_futures_position, calculate_position_size
        if sig.get("mode") == "spot":
            pos = calculate_position_size(sig)
            if not pos.get("usdt_amount"):
                return None
            from config import RISK_CONFIG
            risk = RISK_CONFIG["risk_per_trade"] * 100
            return [f"Ukuran {_idn(pos['position_ratio'], 0)}% modal",
                    f"Rugi maks {_idn(risk, 1)}% modal kalau kena stop"]
        fut = calculate_futures_position(sig)
        if not fut:
            return None
        liq = fut["liquidation_price"]
        risk = f"rugi maks {_idn(fut['_eff_risk'], 1)}% modal kalau kena stop"
        second = f"Likuidasi {_usd(liq, 0)} · {risk}" if 0 < liq < float("inf") else risk[0].upper() + risk[1:]
        return [f"Leverage {fut['leverage']}x · margin {_idn(fut['margin_pct'], 0)}% modal", second]
    except Exception as e:  # noqa: BLE001 — sizing must never cost the alert
        logger.debug("sizing line skipped: %s", e)
        return None


_REASONS = 3


def _reason_lines(sig):
    """The engine's strongest reasons on the signal's side (✓ bullish / ✗ bearish), plain."""
    mark = "✓" if sig.get("type") == "BUY" else "✗"
    picked = [str(r).strip().lstrip("✓✗").strip() for r in (sig.get("reasons") or [])
              if str(r).strip().startswith(mark)][:_REASONS]
    return (["Alasan:"] + [f"• {_esc(r[:70])}" for r in picked]) if picked else []


def _openable(sig):
    # Imported here, as before: signals.market_data pulls in the exchange client.
    from signals.market_data import will_open
    return will_open(sig)


def _veto_text(sig):
    veto = next((str(r) for r in (sig.get("reasons") or []) if "⛔" in str(r)), None)
    if not veto:
        return None
    for key, plain in _VETO_PLAIN:
        if key in veto:
            return plain
    return _esc(veto.replace("⛔", "", 1).strip()[:_VETO_CHARS])


def _score_text(sig):
    """'Skor jual 7,0 (batas 4,9)' for the leading side."""
    buy_s = sig.get("buy_score", 0) or 0
    sell_s = sig.get("sell_score", 0) or 0
    thr = sig.get("_threshold", 0) or 0
    word, lead = ("beli", buy_s) if buy_s >= sell_s else ("jual", sell_s)
    return f"Skor {word} {_idn(lead, 1)} (batas {_idn(thr, 1)})", lead, thr


def _hold_lines(sig):
    mode = sig.get("mode", "futures")
    buy_s = sig.get("buy_score", 0) or 0
    sell_s = sig.get("sell_score", 0) or 0
    text, lead, thr = _score_text(sig)
    if buy_s == sell_s:
        return [f"{text}, tidak ada arah yang jelas"]
    if mode == "spot" and sell_s > buy_s:
        return [f"{text}", "Pasar condong turun; spot hanya bisa beli, jadi menunggu"]
    if lead >= thr:
        veto = _veto_text(sig)
        return [f"{text}, tapi ditolak:", veto] if veto else [f"{text}, tapi ditahan filter berita/makro"]
    return [f"{text}, kurang {_idn(thr - lead, 1)}"]


def _verdict_block(sig):
    """The lines for one mode. Returns (lines, actionable).

    A scored BUY/SELL below the confidence bar CANNOT open: Phase 3 refuses it
    unconditionally, so the card never announces it as a trade. One gated by a Phase 3
    entry check says so, in plain words.
    """
    stype = sig.get("type", "HOLD")
    name = _mode_name(sig.get("mode", "futures")).capitalize()
    if stype in ("BUY", "SELL"):
        text = f"Skor {'beli' if stype == 'BUY' else 'jual'} {_idn(sig.get('strength', 0) or 0, 1)} " \
               f"(batas {_idn(sig.get('_threshold', 0) or 0, 1)})"
        if not _openable(sig):
            conf = _esc(sig.get("confidence") or "?")
            return [f"<b>{name}: sinyal {_side(stype)} tidak dibuka</b>",
                    f"{text}, tapi keyakinan {conf} terlalu rendah"], False
        block = sig.get("_phase3_block")
        if block:
            gate, reason = block
            plain = _GATE_PLAIN.get(gate) or _esc(str(reason)[:_BLOCK_CHARS])
            return [f"<b>{name}: sinyal {_side(stype)} diblokir</b>", f"{text}, tapi ditolak:", plain], False
        icon = "🟢" if stype == "BUY" else "🔴"
        lines = [f"{icon} <b>{name}: {_side(stype)}</b>", text]
        if sig.get("stop_loss") and sig.get("take_profit") and sig.get("entry_price"):
            lv = sig
            if sig.get("mode") == "futures":
                from trading.paper import apply_futures_exit_geometry
                lv = apply_futures_exit_geometry(sig)
            lines += _levels_lines(stype, lv["entry_price"], lv["stop_loss"], lv["take_profit"], lv.get("tp2"))
            lines += _sizing_lines(lv) or []
        lines += _reason_lines(sig)
        return lines, True
    return [f"<b>{name}: tahan</b>"] + _hold_lines(sig), False


def _open_position_lines(prices=None):
    """Open BTC positions, one line each, with the move since entry at this cycle's price."""
    try:
        positions = _sh.get_open_positions()
    except Exception:  # noqa: BLE001
        return []
    if not positions:
        return ["Posisi BTC: tidak ada"]
    lines = ["Posisi BTC:"]
    for p in positions:
        sl = p.get("trailing_stop") or p["stop_loss"]
        tp1 = ", target 1 ✓" if p.get("partial_closed") else ""
        now = (prices or {}).get(p.get("mode"))
        move = ""
        if now and p.get("entry_price"):
            pct = (now - p["entry_price"]) / p["entry_price"] * 100
            move = f", {_idn(pct if p['type'] == 'BUY' else -pct, 2, True)}%"
        lines.append(f"• {_side(p['type'])} {_mode_name(p.get('mode'))} #{p['id']} masuk "
                     f"{_usd(p['entry_price'], 0)}{move}, stop {_usd(sl, 0)}{tp1}")
    return lines


def _account_line():
    """The BTC bot's $1,000 demo account, exactly as the dashboard counts it (run window)."""
    try:
        import json
        from agents.dashboard import db_facts
        from config import DATA_DIR
        import os
        start = None
        try:
            m = json.load(open(os.path.join(DATA_DIR, "paper_run_manifest.json")))
            start = datetime.fromisoformat(m["started_at"])
        except Exception:  # noqa: BLE001
            pass
        acct = db_facts(_sh.SIGNAL_HISTORY_DB, datetime.now(UTC), start).get("account")
        if not acct:
            return None
        chg = (acct["equity"] / acct["start"] - 1) * 100
        return f"Akun Bot BTC {_usd(acct['equity'])} ({_idn(chg, 2, True)}%)"
    except Exception as e:  # noqa: BLE001 — the account line must never cost the card
        logger.debug("account line skipped: %s", e)
        return None


def _render(signals):
    """The one layout both public formatters share."""
    # Futures first: it is the mode that trades most, and its price heads the card.
    signals = sorted((s for s in signals if s), key=lambda s: s.get("mode") == "spot")
    if not signals:
        return ""
    blocks = [(s, *_verdict_block(s)) for s in signals]
    actionable = any(act for _, _, act in blocks)
    price = _price(signals[0])                # futures when present
    time_str = datetime.now(UTC).strftime("%H:%M UTC")
    head = f"{'🔔' if actionable else '⏸'} <b>BTC {_usd(price, 0)}</b>" if price else "⏸ <b>BTC</b>"
    lines = [f"{head} · {_esc(time_str)}",
             "Bot BTC: ada sinyal yang lolos jam ini" if actionable else "Bot BTC: tidak buka posisi jam ini"]
    for _, block, _ in blocks:
        lines.append("")
        lines += block
    prices = {s.get("mode", "futures"): _price(s) for s in signals}
    lines.append("")
    lines += _open_position_lines(prices)
    acct = _account_line()
    if acct:
        lines.append(acct)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def _format_compact_signal_telegram(signal):
    """Single-mode card — same layout as the combined one."""
    return _render([signal])


def _format_consolidated_telegram(spot_signal, futures_signal):
    """Combined hourly card: price, one block per mode, levels only when actionable."""
    return _render([spot_signal, futures_signal])


def _format_close_notification(closed):
    """BTC position closes: result and reason per trade, then the account after them."""
    if not closed:
        return None
    partial_only = all(c.get("outcome") == "TP1" for c in closed)
    lines = (["🎯 <b>Bot BTC ambil untung sebagian</b>", "Setengah posisi ditutup, sisanya tetap berjalan."]
             if partial_only else [])
    for c in closed:
        icon = "✅" if c["pnl"] >= 0 else "❌"
        what = f"{_side(c['type'])} {_mode_name(c.get('mode', 'futures'))}"
        label = _esc(_CLOSE_LABELS.get(c["outcome"], c["outcome"]))
        if not partial_only:
            lines.append(f"{icon} <b>Bot BTC tutup {what}: {_idn(c['pnl'], 2, True)}%</b>")
        else:
            lines.append(f"{what}: {_idn(c['pnl'], 2, True)}% pada setengah posisi")
        lines.append(f"{_usd(c['entry'], 0)} → {_usd(c['exit'], 0)} · {label}")
    acct = _account_line()
    if acct:
        lines.append(acct)
    return "\n".join(lines)


def _format_open_notification(signal, pos_id, mode, pyramid_entry=None):
    """'Position opened' card — sent separately from the hourly one."""
    stype = signal["type"]
    what = f"{_side(stype)} {_mode_name(mode)} #{pos_id}"
    header = (f"🧩 <b>Bot BTC tambah posisi (pyramid {pyramid_entry}): {what}</b>" if pyramid_entry
              else f"🚀 <b>Bot BTC buka {what}</b>")
    lines = [header] + _levels_lines(stype, signal["entry_price"], signal["stop_loss"],
                                     signal["take_profit"], signal.get("tp2"))
    return "\n".join(lines + (_sizing_lines(dict(signal, mode=mode)) or []))


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

