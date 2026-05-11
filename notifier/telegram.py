"""Telegram notification formatters and senders."""

import logging
from datetime import datetime

from trading import history as _sh
from config import FUTURES_CONFIG, RISK_CONFIG, SIGNAL_MAX_SCORE, SPOT_MAX_SCORE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core_analysis import calculate_futures_position, calculate_position_size
from notifier.common import _dir, _esc, _max_score, _mode_label, _macro_banner, _send_telegram_message

logger = logging.getLogger(__name__)
_UP = "▲"
_DOWN = "▼"
_FLAT = "─"


def _format_compact_signal_telegram(signal):
    """Clean vertical signal card — one indicator per line, scannable."""
    stype  = signal["type"]
    label  = _mode_label(signal)
    score  = signal.get("strength", 0)
    mscore = _max_score(signal)
    conf   = signal.get("confidence", "")
    mode   = signal.get("mode", "futures")
    last   = signal.get("_last", {})
    mkt    = signal.get("_market", {})

    buy_s  = signal.get("buy_score", 0)
    sell_s = signal.get("sell_score", 0)
    threshold = signal.get("_threshold", 0)
    lines = []

    # ── Header ──────────────────────────────────────────────
    if stype == "BUY":
        hdr = f"🟢 <b>BUY</b> · {label}"
        if conf:
            hdr += f"  ·  <b>{conf}</b>"
    elif stype == "SELL":
        hdr = f"🔴 <b>SELL</b> · {label}"
        if conf:
            hdr += f"  ·  <b>{conf}</b>"
    else:
        hdr = f"⏸ <b>HOLD</b> · {label}"

    lines.append(hdr)
    score_parts = [f"Score <b>{score:.2f}</b>/{mscore}"]
    if stype == "HOLD":
        gap = threshold - max(buy_s, sell_s)
        if buy_s > sell_s:
            dir_str = "BUY"
        elif sell_s > buy_s:
            dir_str = "BEARISH" if mode == "spot" else "SELL"
        else:
            dir_str = "—"
        score_parts.append(f"Gap <b>{gap:.2f}</b> to {dir_str}")
    lines.append("  ·  ".join(score_parts))

    # ── Trade Setup ─────────────────────────────────────────
    if stype != "HOLD" and signal.get("stop_loss"):
        entry   = signal["entry_price"]
        sl      = signal["stop_loss"]
        tp1     = signal["take_profit"]
        tp2     = signal.get("tp2")
        sl_pct  = abs(entry - sl)  / entry * 100
        tp1_pct = abs(tp1 - entry) / entry * 100
        rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

        lines.append("")
        lines.append("<b>📊 Trade Setup</b>")
        lines.append(f"Entry     <code>${entry:,.0f}</code>")
        lines.append(f"Stop SL   <code>${sl:,.0f}</code>  <code>{sl_pct:+.2f}%</code>")
        lines.append(f"TP1 50%   <code>${tp1:,.0f}</code>  <code>{tp1_pct:+.2f}%</code>")
        if tp2:
            tp2_pct = abs(tp2 - entry) / entry * 100
            lines.append(f"TP2 50%   <code>${tp2:,.0f}</code>  <code>{tp2_pct:+.2f}%</code>")
        lines.append(f"R/R       <b>1:{rr:.2f}</b>")

    # ── Price & Trend ────────────────────────────────────────
    price = signal.get("entry_price", last.get("close", 0))
    if last:
        ema200 = last.get("ema200", 0)
        hi24   = last.get("hi24", 0)
        lo24   = last.get("lo24", 0)
        trend  = _UP if price > ema200 else _DOWN
        trend_label = "BULLISH" if price > ema200 else "BEARISH"

        lines.append("")
        lines.append("<b>📈 Price &amp; Trend</b>")
        lines.append(f"Price     <code>${price:,.0f}</code>")
        lines.append(f"EMA200    <code>${ema200:,.0f}</code>  {trend} {trend_label}")
        lines.append(f"24h Hi    <code>${hi24:,.0f}</code>")
        lines.append(f"24h Lo    <code>${lo24:,.0f}</code>")
        sr = signal.get("support_resistance", {})
        if sr.get("resistance"):
            lines.append(f"Resist    <code>${sr['resistance']:,.0f}</code>")
        if sr.get("support"):
            lines.append(f"Support   <code>${sr['support']:,.0f}</code>")

    # ── Technicals ──────────────────────────────────────────
    if last:
        rsi_v   = last.get("rsi", 0)
        macd    = last.get("macd", 0)
        msig    = last.get("macd_sig", 0)
        sk      = last.get("stoch_k")
        sd      = last.get("stoch_d")
        vwap    = last.get("vwap")
        atr_v   = last.get("atr", 0)
        obv     = last.get("obv_slope", 0)
        div     = signal.get("rsi_divergence", "NONE")

        rsi_tag  = " OB" if rsi_v > 70 else (" OS" if rsi_v < 30 else "")
        macd_str = _UP if macd > msig else _DOWN
        obv_str  = _UP if obv > 0 else _DOWN

        lines.append("")
        lines.append("<b>🔬 Technicals</b>")
        lines.append(f"RSI       <code>{rsi_v:.1f}</code>{rsi_tag}")
        lines.append(f"MACD      <code>{macd:.0f}</code>  {macd_str}")
        if sk is not None and sd is not None:
            sk_tag = " OS" if sk < 20 else (" OB" if sk > 80 else "")
            lines.append(f"StochRSI  <code>{sk:.1f} / {sd:.1f}</code>{sk_tag}")
        if vwap:
            vwap_str = _UP if price > vwap else _DOWN
            lines.append(f"VWAP      <code>${vwap:,.0f}</code>  {vwap_str}")
        lines.append(f"ATR       <code>${atr_v:,.0f}</code>")
        lines.append(f"OBV       <code>{obv:,.0f}</code>  {obv_str}")
        if div and str(div).strip() not in ("", "NONE", "─"):
            lines.append(f"RSI Div   {_esc(str(div))}")

    # ── HTF ─────────────────────────────────────────────────
    htf = signal.get("_htf", {})
    if htf:
        lines.append("")
        lines.append("<b>⏱ HTF</b>")
        tf_order = ["1d", "1w"] if mode == "spot" else ["4h", "1d"]
        for key in tf_order:
            if key not in htf:
                continue
            ind = htf.get(f"{key}_indicators", {})
            rsi = ind.get("rsi")
            macd_dir = _UP if ind.get("macd") == "BULLISH" else (_DOWN if ind.get("macd") == "BEARISH" else _FLAT)
            vol_trend = ind.get("vol_trend", "FLAT")
            vt = {"RISING": _UP, "FALLING": _DOWN, "FLAT": _FLAT}.get(vol_trend, _FLAT)
            extra = []
            if rsi is not None:
                extra.append(f"RSI {rsi:.0f}")
            if ind.get("macd"):
                extra.append(f"MACD {macd_dir}")
            extra.append(f"Vol {vt}")
            trend_str = htf[key]
            lines.append(f"{key.upper():<7}  <b>{trend_str}</b>  " + "  ".join(extra))
        aligned = "✓ ALIGNED" if htf.get("aligned") else "✗ DIVERGING"
        lines.append(f"         {aligned}")

    # ── Market Structure ────────────────────────────────────
    if mkt:
        lines.append("")
        lines.append("<b>🏦 Market</b>")

        if mode == "futures":
            funding  = mkt.get("funding", {})
            ls       = mkt.get("long_short", {})
            oi       = mkt.get("open_interest", {})
            fund_pct = funding.get("rate_pct", 0)
            ls_ratio = ls.get("ratio", 1)
            oi_val   = oi.get("notional", 0)
            oi_dir   = _dir(oi.get("change_pct", 0))
            fund_dir = _UP if fund_pct < 0 else _DOWN
            ls_dir   = _UP if ls_ratio < 1 else _DOWN
            basis_pct = funding.get("basis_pct", 0)

            lines.append(f"Funding   <code>{fund_pct:+.5f}%</code>  {fund_dir}")
            lines.append(f"L/S       <code>{ls_ratio:.2f}</code>  {ls_dir}")
            if oi_val:
                lines.append(f"OI        <code>${oi_val/1e9:.2f}B</code>  {oi_dir}")
            if basis_pct:
                lines.append(f"Basis     <code>{basis_pct:+.4f}%</code>")

        dxy    = mkt.get("dxy", {})
        sp500  = mkt.get("sp500", {})
        btcdom = mkt.get("btc_dom", {})
        stable = mkt.get("stablecoin", {})

        dxy_c = dxy.get("current", 0)
        dxy_d = dxy.get("change_pct", 0)
        sp_c  = sp500.get("current", 0)
        sp_d  = sp500.get("change_pct", 0)
        sp_dir = _UP if sp_d > 0 else _DOWN
        btcd     = btcdom.get("current", 0)
        btcd_dir = _dir(btcdom.get("change_pct", 0))
        stab     = stable.get("total_b", 0)
        stab_dir = _dir(stable.get("change_pct", 0))

        if sp_c:
            lines.append(f"S&amp;P      <code>{sp_c:,.0f}</code>  <code>{sp_d:+.2f}%</code>  {sp_dir}")
        if dxy_c:
            lines.append(f"DXY       <code>{dxy_c:.3f}</code>  <code>{dxy_d:+.2f}%</code>")
        if btcd:
            lines.append(f"BTC.D     <code>{btcd:.1f}%</code>  {btcd_dir}")
        if stab:
            lines.append(f"Stable    <code>${stab:.0f}B</code>  {stab_dir}")

    # ── Sentiment ───────────────────────────────────────────
    fng_val = signal.get("fear_greed_value", 50)
    fng_lbl = signal.get("fear_greed_label", "Neutral")
    news    = signal.get("news_sentiment", "NEUTRAL")
    news_conf = signal.get("news_confidence", 0)
    lines.append("")
    lines.append("<b>📰 Sentiment</b>")
    lines.append(f"F&amp;G       <code>{fng_val}/100</code>  {_esc(fng_lbl)}")
    lines.append(f"News      {_esc(news)}  <code>{news_conf:.0f}%</code>")

    # ── Reasons ─────────────────────────────────────────────
    reasons = signal.get("reasons", [])
    if reasons:
        lines.append("")
        lines.append(f"<b>✅ Reasons</b> ({len(reasons)})")
        for r in reasons[:10]:
            if r.startswith("✓"):
                sym, body = "✓", r[2:].strip()
            elif r.startswith("✗"):
                sym, body = "✗", r[2:].strip()
            elif r.startswith("⚠"):
                sym, body = "⚠", r[2:].strip()
            elif r.startswith("•"):
                sym, body = "•", r[2:].strip()
            else:
                sym, body = "•", r.strip()
            lines.append(f"{sym} {_esc(body[:70])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Close & position notifications
# ---------------------------------------------------------------------------

def _format_close_notification(closed):
    """Short notification for position closes (TP/SL/MACRO)."""
    if not closed:
        return None

    lines = ["🔔 <b>Position Closed</b>"]

    for c in closed:
        icon   = "🟢" if c["type"] == "BUY" else "🔴"
        mode   = c.get("mode", "fut")[:3].upper()
        pnl    = c["pnl"]
        pnl_s  = f"+{pnl:.2f}%" if pnl >= 0 else f"{pnl:.2f}%"
        outcome = c["outcome"]
        label = {
            "TP1": "TP1 hit · trailing→BE",
            "TP2": "TP2 hit · full win",
            "Trail": "trailing stop",
            "SL": "stop loss",
            "MACRO_CLOSE": "macro force-close",
            "FLIP": "signal reversed · flip",
        }.get(outcome, outcome)

        lines.append(
            f"{icon} {mode} {c['type']}  <b>{pnl_s}</b>  {label}\n"
            f"   <code>${c['entry']:,.0f}</code> → <code>${c['exit']:,.0f}</code>"
        )

    return "\n".join(lines)


def _format_open_notification(signal, pos_id, mode, pyramid_entry=None):
    """Dedicated 'Position Opened' Telegram card — separate from main signal."""
    stype     = signal["type"]
    entry     = signal["entry_price"]
    sl        = signal["stop_loss"]
    tp1       = signal["take_profit"]
    tp2       = signal.get("tp2")
    sl_pct    = abs(entry - sl) / entry * 100
    tp1_pct   = abs(tp1 - entry) / entry * 100
    rr        = tp1_pct / sl_pct if sl_pct > 0 else 0

    icon  = "🟢" if stype == "BUY" else "🔴"
    label = "SPOT" if mode == "spot" else "FUTURES"

    if pyramid_entry:
        header = f"🧩 <b>Pyramid Entry #{pyramid_entry} — {label}</b>"
    else:
        header = f"🚀 <b>Position Opened — {label}</b>"

    lines = [
        header,
        f"{icon} <b>{stype}</b> @ <code>${entry:,.0f}</code>  ·  #{pos_id}",
        f"SL      <code>${sl:,.0f}</code>  (<code>-{sl_pct:.2f}%</code>)",
        f"TP1     <code>${tp1:,.0f}</code>  (<code>+{tp1_pct:.2f}%</code>)",
    ]
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        lines.append(f"TP2     <code>${tp2:,.0f}</code>  (<code>+{tp2_pct:.2f}%</code>)")
    lines.append(f"R/R     <b>1:{rr:.2f}</b>")

    return "\n".join(lines)


def _format_consolidated_telegram(spot_signal, futures_signal):
    """Single consolidated message — all sections in one clean vertical card."""
    lines = []
    primary = futures_signal or spot_signal
    last = primary.get("_last", {}) if primary else {}
    mkt = primary.get("_market", {}) if primary else {}
    news = primary.get("_news_data") or {} if primary else {}

    # ── Header ─────────────────────────────────────────────────
    time_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines.append(f"🔔 <b>SpotSignal · BTC/USDT</b>")
    lines.append(f"<code>{_esc(time_str)}</code>")
    lines.append("")

    # ── Signal Verdicts ────────────────────────────────────────
    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        stype  = sig["type"]
        label  = _mode_label(sig)
        score  = sig.get("strength", 0)
        mscore = _max_score(sig)
        mode   = sig.get("mode", "futures")
        conf   = sig.get("confidence", "")
        buy_s  = sig.get("buy_score", 0)
        sell_s = sig.get("sell_score", 0)
        threshold = sig.get("_threshold", 0)

        if stype == "BUY":
            line = f"🟢 <b>BUY · {label}</b>"
            if conf:
                line += f" · {conf}"
            line += f"  Score <b>{score:.2f}</b>/{mscore}"
        elif stype == "SELL":
            line = f"🔴 <b>SELL · {label}</b>"
            if conf:
                line += f" · {conf}"
            line += f"  Score <b>{score:.2f}</b>/{mscore}"
        else:
            gap = threshold - max(buy_s, sell_s)
            if buy_s > sell_s:
                dir_str = "BUY"
            elif sell_s > buy_s:
                dir_str = "BEARISH" if mode == "spot" else "SELL"
            else:
                dir_str = "NEUTRAL"
            if dir_str == "NEUTRAL":
                line = f"⏸ <b>HOLD · {label}</b>  Score <b>{score:.2f}</b>/{mscore}  NEUTRAL · gap {gap:.2f}"
            elif gap <= 0 and mode == "spot" and dir_str == "BEARISH":
                line = f"⏸ <b>HOLD · {label}</b>  Score <b>{score:.2f}</b>/{mscore}  {dir_str} · BUY‑only → HOLD"
            elif gap <= 0:
                line = f"⏸ <b>HOLD · {label}</b>  Score <b>{score:.2f}</b>/{mscore}  {dir_str} leads · gap 0.00 READY"
            else:
                line = f"⏸ <b>HOLD · {label}</b>  Score <b>{score:.2f}</b>/{mscore}  {dir_str} leads · gap {gap:.2f}"

        lines.append(line)
    lines.append("")

    # ── Price & Trend ──────────────────────────────────────────
    if last:
        price = primary.get("entry_price", last.get("close", 0))
        ema200 = last.get("ema200", 0)
        hi24 = last.get("hi24", 0)
        lo24 = last.get("lo24", 0)
        trend = _UP if price > ema200 else _DOWN
        trend_label = "BULLISH" if price > ema200 else "BEARISH"

        lines.append("<b>━━━ 📈 PRICE &amp; TREND ━━━</b>")
        lines.append(f"Price     <code>${price:,.0f}</code>")
        lines.append(f"EMA200    <code>${ema200:,.0f}</code>  {trend} {trend_label}")
        lines.append(f"24h Hi    <code>${hi24:,.0f}</code>")
        lines.append(f"24h Lo    <code>${lo24:,.0f}</code>")

        sr = primary.get("support_resistance", {})
        if sr.get("resistance"):
            lines.append(f"Resist    <code>${sr['resistance']:,.0f}</code>")
        if sr.get("support"):
            lines.append(f"Support   <code>${sr['support']:,.0f}</code>")
        lines.append("")

    # ── Market Structure ───────────────────────────────────────
    if mkt:
        lines.append("<b>━━━ 🏦 MARKET STRUCTURE ━━━</b>")

        if futures_signal:
            funding  = mkt.get("funding", {})
            ls       = mkt.get("long_short", {})
            oi       = mkt.get("open_interest", {})
            fund_pct = funding.get("rate_pct", 0)
            ls_ratio = ls.get("ratio", 1)
            oi_val   = oi.get("notional", 0)
            basis_pct = funding.get("basis_pct", 0)
            fund_dir  = _UP if fund_pct < 0 else _DOWN
            ls_dir    = _UP if ls_ratio < 1 else _DOWN
            oi_dir    = _dir(oi.get("change_pct", 0))

            lines.append(f"Funding   <code>{fund_pct:+.5f}%</code>  {fund_dir}")
            lines.append(f"L/S       <code>{ls_ratio:.2f}</code>  {ls_dir}")
            if oi_val and oi_val > 1e6:
                lines.append(f"OI        <code>${oi_val/1e9:.2f}B</code>  {oi_dir}")
            if basis_pct:
                lines.append(f"Basis     <code>{basis_pct:+.4f}%</code>")

        dxy    = mkt.get("dxy", {})
        sp500  = mkt.get("sp500", {})
        btcdom = mkt.get("btc_dom", {})
        stable = mkt.get("stablecoin", {})
        gold   = mkt.get("gold", {})
        vix    = mkt.get("vix", {})

        dxy_c = dxy.get("current", 0)
        dxy_d = dxy.get("change_pct", 0)
        sp_c  = sp500.get("current", 0)
        sp_d  = sp500.get("change_pct", 0)
        sp_dir = _UP if sp_d > 0 else _DOWN
        btcd     = btcdom.get("current", 0)
        btcd_dir = _dir(btcdom.get("change_pct", 0))
        stab     = stable.get("total_b", 0)
        stab_dir = _dir(stable.get("change_pct", 0))
        gold_c = gold.get("current", 0)
        gold_d = gold.get("change_pct", 0)
        vix_c  = vix.get("current", 0)
        vix_d  = vix.get("change_pct", 0)

        if dxy_c:
            lines.append(f"DXY       <code>{dxy_c:.3f}</code>  <code>{dxy_d:+.2f}%</code>")
        if sp_c:
            lines.append(f"S&amp;P      <code>{sp_c:,.0f}</code>  <code>{sp_d:+.2f}%</code>  {sp_dir}")
        if btcd:
            lines.append(f"BTC.D     <code>{btcd:.1f}%</code>  {btcd_dir}")
        if stab:
            lines.append(f"Stable    <code>${stab:.0f}B</code>  {stab_dir}")
        if gold_c:
            gold_dir = _UP if gold_d > 0 else _DOWN
            lines.append(f"Gold      <code>${gold_c:,.0f}</code>  <code>{gold_d:+.2f}%</code>  {gold_dir}")
        if vix_c:
            vix_dir = _UP if vix_d > 0 else _DOWN
            lines.append(f"VIX       <code>{vix_c:.2f}</code>  <code>{vix_d:+.2f}%</code>  {vix_dir}")
        lines.append("")

    # ── Technicals per mode ─────────────────────────────────────
    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        mode   = sig.get("mode", "futures")
        tf_lbl = "SPOT 4H" if mode == "spot" else "FUTURES 1H"
        _last  = sig.get("_last", {})
        if not _last:
            continue

        lines.append(f"<b>━━━ 🔬 TECHNICALS — {tf_lbl} ━━━</b>")

        rsi_v  = _last.get("rsi", 0)
        macd   = _last.get("macd", 0)
        msig_v = _last.get("macd_sig", 0)
        sk     = _last.get("stoch_k")
        sd     = _last.get("stoch_d")
        vwap   = _last.get("vwap")
        atr_v  = _last.get("atr", 0)
        obv    = _last.get("obv_slope", 0)
        price  = sig.get("entry_price", _last.get("close", 0))
        div    = sig.get("rsi_divergence", "NONE")
        cs     = sig.get("candlestick", {})
        regime = sig.get("regime", "")
        adx_v  = sig.get("adx")

        rsi_tag   = "  <b>OB</b>" if rsi_v > 70 else ("  <b>OS</b>" if rsi_v < 30 else "")
        macd_dir  = _UP if macd > msig_v else _DOWN
        obv_dir   = _UP if obv > 0 else _DOWN

        lines.append(f"RSI       <code>{rsi_v:.1f}</code>{rsi_tag}")
        lines.append(f"MACD      <code>{macd:+.0f}</code>  {macd_dir}  sig <code>{msig_v:+.0f}</code>")
        if sk is not None and sd is not None:
            sk_tag = "  <b>OB</b>" if sk > 80 else ("  <b>OS</b>" if sk < 20 else "")
            lines.append(f"StochRSI  <code>{sk:.0f}/{sd:.0f}</code>{sk_tag}")
        if vwap:
            vwap_dir = _UP if price > vwap else _DOWN
            lines.append(f"VWAP      <code>${vwap:,.0f}</code>  {vwap_dir}")
        lines.append(f"ATR       <code>${atr_v:,.0f}</code>")
        lines.append(f"OBV       <code>{obv:+,.0f}</code>  {obv_dir}")
        if div and str(div).strip() not in ("", "NONE"):
            div_icon = "🟢" if div == "BULLISH" else "🔴"
            lines.append(f"RSI Div   {div_icon} {_esc(div)}")
        if cs:
            bull_cs = cs.get("bullish")
            bear_cs = cs.get("bearish")
            if bull_cs:
                lines.append(f"Candle    🟢 {_esc(bull_cs.replace('_', ' '))}")
            if bear_cs:
                lines.append(f"Candle    🔴 {_esc(bear_cs.replace('_', ' '))}")
        if regime:
            adx_str = f"  ADX <code>{adx_v:.1f}</code>" if adx_v is not None else ""
            lines.append(f"Regime    <b>{_esc(regime)}</b>{adx_str}")
        lines.append("")

    # ── HTF per mode ────────────────────────────────────────────
    htf_printed = False
    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        mode   = sig.get("mode", "futures")
        tf_lbl = "SPOT 4H" if mode == "spot" else "FUTURES 1H"
        htf    = sig.get("_htf", {})
        if not htf:
            continue
        if not htf_printed:
            lines.append("<b>━━━ ⏱ HTF ALIGNMENT ━━━</b>")
            htf_printed = True

        aligned_str = "✓ ALIGNED" if htf.get("aligned") else "✗ DIVERGING"
        lines.append(f"<b>{tf_lbl}</b>  {aligned_str}")
        tf_order = ["1d", "1w"] if mode == "spot" else ["4h", "1d"]
        for key in tf_order:
            if key not in htf:
                continue
            ind        = htf.get(f"{key}_indicators", {})
            rsi_h      = ind.get("rsi")
            macd_h     = ind.get("macd", "")
            vol_h      = ind.get("vol_trend", "FLAT")
            macd_icon  = _UP if macd_h == "BULLISH" else (_DOWN if macd_h == "BEARISH" else _FLAT)
            vol_icon   = {"RISING": _UP, "FALLING": _DOWN, "FLAT": _FLAT}.get(vol_h, _FLAT)
            trend_str  = htf[key]
            trend_icon = "🟢" if trend_str == "BULLISH" else "🔴"
            rsi_str    = f"  RSI <code>{rsi_h:.0f}</code>" if rsi_h is not None else ""
            lines.append(f"  {key.upper():<4} {trend_icon} <b>{trend_str}</b>{rsi_str}  MACD {macd_icon}  Vol {vol_icon}")
        lines.append("")

    # ── Top Headlines ──────────────────────────────────────────
    headlines = news.get("headlines", [])
    if headlines:
        lines.append("<b>━━━ 📰 TOP HEADLINES ━━━</b>")
        for h in headlines[:5]:
            icon = _UP if h["sentiment"] > 0 else (_DOWN if h["sentiment"] < 0 else _FLAT)
            lines.append(f"{icon} {_esc(h['title'][:72])}")
        lines.append("")

    # ── Performance ────────────────────────────────────────────
    lines.append("<b>━━━ 📈 PERFORMANCE ━━━</b>")
    try:
        for label_str, mode_str in [("SPOT", "spot"), ("FUTURES", "futures")]:
            pnl, cnt, avg = _sh.get_closed_pnl(mode_str)
            if cnt == 0:
                lines.append(f"<b>{label_str}</b>  No closed trades yet")
                continue
            bd   = _sh.get_outcome_breakdown(mode_str)
            wins = bd.get("WIN", 0)
            loss = bd.get("LOSS", 0)
            mac  = bd.get("MACRO_CLOSE", 0)
            pf   = _sh.get_profit_factor(mode_str)
            wr   = _sh.get_win_rate(mode_str)

            outcomes = f"{wins} Wins · {loss} Losses"
            if mac:
                outcomes += f" · {mac} Macro"

            lines.append(f"<b>📊 {label_str}</b>")
            lines.append(f"Total Trades  <code>{cnt}</code>")
            lines.append(f"Net P&amp;L      <b>{pnl:+.2f}%</b>")
            lines.append(f"Avg / Trade   <code>{avg:+.2f}%</code>")
            lines.append(f"Outcomes      {outcomes}")
            if (wins + loss) > 0:
                wr_str = f"{wr*100:.1f}%" if wr is not None else "—"
                pf_str = f"{pf:.2f}" if pf is not None and pf != float('inf') else ("∞" if pf == float('inf') else "—")
                lines.append(f"Win Rate      <code>{wr_str}</code>")
                lines.append(f"Profit Factor <code>{pf_str}</code>")
            lines.append("")
    except Exception:
        lines.append("—")
        lines.append("")

    # ── Position Sizing ────────────────────────────────────────
    lines.append("<b>━━━ 📊 POSITION SIZING ━━━</b>")
    lines.append("")

    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        mode    = sig.get("mode", "futures")
        stype   = sig["type"]
        is_active = stype != "HOLD" and sig.get("stop_loss")
        last_sig = sig.get("_last", {})
        atr      = last_sig.get("atr", 0)
        entry_px = sig.get("entry_price", last_sig.get("close", 0))
        buy_s    = sig.get("buy_score", 0)
        sell_s   = sig.get("sell_score", 0)
        direction = "BUY" if buy_s > sell_s else ("SELL" if sell_s > buy_s else "neutral")

        if mode == "spot":
            balance = RISK_CONFIG["account_balance"]
            try:
                sp_pnl, sp_closed, _ = _sh.get_closed_pnl(mode="spot")
            except Exception:
                sp_pnl, sp_closed = 0, 0
            now_bal = balance * (1 + sp_pnl / 100)

            header = f"<b>SPOT</b>  Balance <code>${balance:,.0f}</code>"
            if sp_closed > 0:
                sgn = "+" if sp_pnl >= 0 else ""
                header += f" → <code>${now_bal:,.0f}</code>  <code>{sgn}{sp_pnl:.1f}%</code>"
            lines.append(header)

            if is_active:
                sl     = sig["stop_loss"]
                tp     = sig["take_profit"]
                sl_pct = abs(entry_px - sl) / entry_px * 100
                tp_pct = abs(tp - entry_px) / entry_px * 100
                rr     = tp_pct / sl_pct if sl_pct > 0 else 0
                pos    = calculate_position_size(sig)

                lines.append(f"Entry     <code>${entry_px:,.0f}</code>")
                lines.append(f"Stop SL   <code>${sl:,.0f}</code>  <code>-{sl_pct:.2f}%</code>")
                lines.append(f"Take TP   <code>${tp:,.0f}</code>  <code>+{tp_pct:.2f}%</code>")
                lines.append(f"R/R       <b>1:{rr:.2f}</b>")
                if pos.get("usdt_amount"):
                    lines.append(f"Position  <code>${pos['usdt_amount']:,.0f}</code> ({pos['position_ratio']:.1f}%)")
                lines.append(f"Max Risk  <code>${pos.get('risk_amount', 0):,.0f}</code> ({RISK_CONFIG['risk_per_trade']*100:.0f}%/trade)")
            else:
                lines.append(f"Entry     <code>${entry_px:,.0f}</code>")
                if atr > 0 and direction != "neutral":
                    sl_dist = atr * RISK_CONFIG["atr_multiplier"]
                    tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
                    if direction == "BUY":
                        hypo_sl = entry_px - sl_dist
                        hypo_tp = entry_px + tp_dist
                    else:
                        hypo_sl = entry_px + sl_dist
                        hypo_tp = entry_px - tp_dist
                    lines.append(f"Stop SL   <code>~${hypo_sl:,.0f}</code> <i>(estimate, not active)</i>")
                    lines.append(f"Take TP   <code>~${hypo_tp:,.0f}</code> <i>(estimate, not active)</i>")
                else:
                    lines.append("Stop SL   —")
                    lines.append("Take TP   —")
                lines.append(f"R/R       <b>1:{RISK_CONFIG['take_profit_rr']:.1f}</b>")
                risk_amt = balance * RISK_CONFIG["risk_per_trade"]
                lines.append(f"Max Risk  <code>${risk_amt:,.0f}</code> ({RISK_CONFIG['risk_per_trade']*100:.0f}%/trade)")

        else:  # futures
            balance = FUTURES_CONFIG["futures_balance"]
            try:
                fu_pnl, fu_closed, _ = _sh.get_closed_pnl(mode="futures")
            except Exception:
                fu_pnl, fu_closed = 0, 0
            now_bal = balance * (1 + fu_pnl / 100)

            header = f"<b>FUTURES</b>  Balance <code>${balance:,.0f}</code>"
            if fu_closed > 0:
                sgn = "+" if fu_pnl >= 0 else ""
                header += f" → <code>${now_bal:,.0f}</code>  <code>{sgn}{fu_pnl:.1f}%</code>"
            lines.append(header)

            if is_active:
                sl     = sig["stop_loss"]
                tp     = sig["take_profit"]
                sl_pct = abs(entry_px - sl) / entry_px * 100
                tp_pct = abs(tp - entry_px) / entry_px * 100
                rr     = tp_pct / sl_pct if sl_pct > 0 else 0
                fut    = calculate_futures_position(sig)

                lines.append(f"Entry     <code>${entry_px:,.0f}</code>")
                lines.append(f"Stop SL   <code>${sl:,.0f}</code>  <code>-{sl_pct:.2f}%</code>")
                lines.append(f"Take TP   <code>${tp:,.0f}</code>  <code>+{tp_pct:.2f}%</code>")
                lines.append(f"R/R       <b>1:{rr:.2f}</b>")
                if fut:
                    lines.append(f"Direction <b>{fut['direction']}</b>  {fut['leverage']}x [{fut['tier']}]")
                    lines.append(f"Margin    <code>${fut['margin']:,.0f}</code> ({fut['margin_pct']:.1f}%)")
                    lines.append(f"Pos Val   <code>${fut['position_value']:,.0f}</code>")
                    lines.append(f"Liq.      <code>${fut['liquidation_price']:,.0f}</code>")
                    lines.append(f"Max Risk  <code>${fut['risk_amount']:,.0f}</code> ({FUTURES_CONFIG['risk_per_trade']*100:.0f}%/trade)")
                else:
                    risk_amt = balance * FUTURES_CONFIG["risk_per_trade"]
                    lines.append(f"Max Risk  <code>${risk_amt:,.0f}</code> ({FUTURES_CONFIG['risk_per_trade']*100:.0f}%/trade)")
            else:
                lines.append(f"Entry     <code>${entry_px:,.0f}</code>")
                if atr > 0 and direction != "neutral":
                    sl_dist = atr * RISK_CONFIG["atr_multiplier"]
                    tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
                    if direction == "BUY":
                        hypo_sl = entry_px - sl_dist
                        hypo_tp = entry_px + tp_dist
                    else:
                        hypo_sl = entry_px + sl_dist
                        hypo_tp = entry_px - tp_dist
                    lines.append(f"Stop SL   <code>~${hypo_sl:,.0f}</code> <i>(estimate, not active)</i>")
                    lines.append(f"Take TP   <code>~${hypo_tp:,.0f}</code> <i>(estimate, not active)</i>")
                else:
                    lines.append("Stop SL   —")
                    lines.append("Take TP   —")
                lines.append(f"R/R       <b>1:{RISK_CONFIG['take_profit_rr']:.1f}</b>")
                risk_amt = balance * FUTURES_CONFIG["risk_per_trade"]
                lines.append(f"Max Risk  <code>${risk_amt:,.0f}</code> ({FUTURES_CONFIG['risk_per_trade']*100:.0f}%/trade)")

        lines.append("")

    # ── Verdict ────────────────────────────────────────────────
    lines.append("<b>━━━ 📝 VERDICT ━━━</b>")
    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        mode      = sig.get("mode", "futures")
        stype     = sig["type"]
        if mode == "spot":
            label = "SPOT"
        elif stype == "BUY":
            label = "FUTURES LONG"
        elif stype == "SELL":
            label = "FUTURES SHORT"
        else:
            label = "FUTURES"
        buy_s     = sig.get("buy_score", 0)
        sell_s    = sig.get("sell_score", 0)
        score     = sig.get("strength", 0)
        mscore    = _max_score(sig)
        threshold = sig.get("_threshold", 0)
        conf      = sig.get("confidence", "")

        if stype == "BUY":
            conf_str = f"  {conf}" if conf else ""
            lines.append(f"🟢 <b>{label}  BUY 🔥  {score:.2f}/{mscore}  ≥ thr {threshold:.2f}{conf_str}</b>")
            sub = f"B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>"
            if conf == "STRONG" and mode == "spot":
                sub += "  · pyramid eligible"
            lines.append(f"  {sub}")
            # Action guidance
            action_word = "LONG" if mode == "futures" else "BUY"
            if conf == "STRONG":
                action = f"▶ <b>ENTER {action_word}</b> — strong signal · full size · SL/TP set above"
            elif conf == "WEAK":
                action = f"▶ <b>ENTER {action_word}</b> — weak signal · reduce size · watch closely"
            else:
                action = f"▶ <b>ENTER {action_word}</b> — signal confirmed · SL/TP set above"
            lines.append(f"  {action}")

        elif stype == "SELL":
            conf_str = f"  {conf}" if conf else ""
            lines.append(f"🔴 <b>{label}  SELL 🔥  {score:.2f}/{mscore}  ≥ thr {threshold:.2f}{conf_str}</b>")
            lines.append(f"  B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>")
            if conf == "STRONG":
                action = "▶ <b>ENTER SHORT</b> — strong signal · full size · SL/TP set above"
            elif conf == "WEAK":
                action = "▶ <b>ENTER SHORT</b> — weak signal · reduce size · watch closely"
            else:
                action = "▶ <b>ENTER SHORT</b> — signal confirmed · SL/TP set above"
            lines.append(f"  {action}")

        else:
            if buy_s > sell_s:
                dir_str, lead = "BUY", buy_s
            elif sell_s > buy_s:
                dir_str = "BEARISH" if mode == "spot" else "SELL"
                lead = sell_s
            else:
                dir_str, lead = "NEUTRAL", max(buy_s, sell_s)
            gap = threshold - lead

            if mode == "spot" and dir_str == "BEARISH":
                lines.append(f"⏸ <b>{label}  HOLD  {score:.2f}/{mscore}  BEARISH · BUY-only</b>")
                lines.append(f"  B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>")
                lines.append("  ▶ <b>SKIP</b> — bearish pressure · spot BUY-only · no trade")
            elif gap < 0:
                lines.append(f"⏸ <b>{label}  HOLD  {score:.2f}/{mscore}  news downgrade</b>")
                lines.append(f"  B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>  {dir_str} leads")
                lines.append("  ▶ <b>SKIP</b> — score met but macro news blocked entry")
            elif dir_str == "NEUTRAL":
                lines.append(f"⏸ <b>{label}  HOLD  {score:.2f}/{mscore}  NEUTRAL</b>")
                lines.append(f"  B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>")
                lines.append("  ▶ <b>SKIP</b> — no clear direction · wait for setup")
            else:
                lines.append(f"⏸ <b>{label}  HOLD  {score:.2f}/{mscore}  gap {gap:.2f} to fire</b>")
                lines.append(f"  B:<b>{buy_s:.2f}</b>  S:<b>{sell_s:.2f}</b>  {dir_str} leads")
                lines.append(f"  ▶ <b>SKIP</b> — {dir_str} building · need <b>+{gap:.2f}</b> more · SL/TP estimates above")

        reasons = sig.get("reasons", [])
        for r in reasons[:12]:
            if r.startswith("✓"):
                sym, body = "✓", r[2:].strip()
            elif r.startswith("✗"):
                sym, body = "✗", r[2:].strip()
            elif r.startswith("⚠"):
                sym, body = "⚠", r[2:].strip()
            elif r.startswith("📊"):
                sym, body = "·", r[2:].strip()
            else:
                sym, body = "·", r.strip()
            lines.append(f"  {sym} {_esc(body[:80])}")
        lines.append("")

    # ── Open Positions ─────────────────────────────────────────
    try:
        open_pos = _sh.get_open_positions()
        lines.append("<b>━━━ 📂 OPEN POSITIONS ━━━</b>")
        if open_pos:
            for p in open_pos:
                icon        = "🟢" if p["type"] == "BUY" else "🔴"
                mode_lbl    = "SPOT" if p.get("mode") == "spot" else "FUTURES"
                entry       = p["entry_price"]
                sl          = p.get("trailing_stop") or p["stop_loss"]
                tp1         = p["take_profit"]
                tp2         = p.get("tp2")
                partial     = p.get("partial_closed", 0)
                opened      = str(p.get("opened_at", ""))[:16]
                pyr         = p.get("pyramid_entry", 0)
                pyr_str     = f"  #pyr{pyr}" if pyr else ""
                partial_str = "  · TP1 ✓ trail→BE" if partial else ""
                lines.append(f"{icon} <b>{mode_lbl} {p['type']}  #{p['id']}</b>  @ <code>${entry:,.0f}</code>{pyr_str}")
                lines.append(f"   SL <code>${sl:,.0f}</code>  TP1 <code>${tp1:,.0f}</code>" + (f"  TP2 <code>${tp2:,.0f}</code>" if tp2 else "") + partial_str)
                lines.append(f"   Opened {opened}")
        else:
            lines.append("No open positions")
        lines.append("")
    except Exception:
        pass

    lines.append("ⓘ  hobby · study · experiment — not financial advice")

    return "\n".join(lines)



def _send_combined_telegram(spot_signal, futures_signal, symbol):
    """Send single consolidated message + optional macro banner."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

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

