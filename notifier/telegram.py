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
        }.get(outcome, outcome)

        lines.append(
            f"{icon} {mode} {c['type']}  <b>{pnl_s}</b>  {label}\n"
            f"   <code>${c['entry']:,.0f}</code> → <code>${c['exit']:,.0f}</code>"
        )

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
    lines.append(f"🔔 <b>CrySignal · BTC/USDT</b>")
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

        dxy_c = dxy.get("current", 0)
        dxy_d = dxy.get("change_pct", 0)
        sp_c  = sp500.get("current", 0)
        sp_d  = sp500.get("change_pct", 0)
        sp_dir = _UP if sp_d > 0 else _DOWN
        btcd     = btcdom.get("current", 0)
        btcd_dir = _dir(btcdom.get("change_pct", 0))
        stab     = stable.get("total_b", 0)
        stab_dir = _dir(stable.get("change_pct", 0))

        if dxy_c:
            lines.append(f"DXY       <code>{dxy_c:.3f}</code>  <code>{dxy_d:+.2f}%</code>")
        if sp_c:
            lines.append(f"S&amp;P      <code>{sp_c:,.0f}</code>  <code>{sp_d:+.2f}%</code>  {sp_dir}")
        if btcd:
            lines.append(f"BTC.D     <code>{btcd:.1f}%</code>  {btcd_dir}")
        if stab:
            lines.append(f"Stable    <code>${stab:.0f}B</code>  {stab_dir}")
        lines.append("")

    # ── Top Headlines ──────────────────────────────────────────
    headlines = news.get("headlines", [])
    if headlines:
        lines.append("<b>━━━ 📰 TOP HEADLINES ━━━</b>")
        for h in headlines[:5]:
            icon = _UP if h["sentiment"] > 0 else (_DOWN if h["sentiment"] < 0 else _FLAT)
            cat = "G" if h.get("category") == "geopolitical" else "C"
            lines.append(f"{icon} {cat}  {_esc(h['title'][:68])}")
        lines.append("")

    # ── Performance ────────────────────────────────────────────
    try:
        spot_pnl, spot_cnt, _ = _sh.get_closed_pnl("spot")
        fut_pnl, fut_cnt, _   = _sh.get_closed_pnl("futures")
        total_trades = spot_cnt + fut_cnt

        if total_trades > 0:
            lines.append("<b>━━━ 📉 PERFORMANCE (all-time) ━━━</b>")
            if spot_cnt > 0:
                lines.append(f"SPOT     {spot_cnt} trades  <b>{spot_pnl:+.2f}%</b>")
            if fut_cnt > 0:
                lines.append(f"FUTURES  {fut_cnt} trades  <b>{fut_pnl:+.2f}%</b>")
            if spot_cnt > 0 and fut_cnt > 0:
                lines.append(f"Total    {total_trades} trades  <b>{spot_pnl + fut_pnl:+.2f}%</b>")

            bd = _sh.get_outcome_breakdown()
            w  = bd.get("WIN", 0)
            l  = bd.get("LOSS", 0)
            mc = bd.get("MACRO_CLOSE", 0)
            be = bd.get("BREAKEVEN", 0)
            wr_str = f"WR {w/(w+l)*100:.0f}%" if (w + l) > 0 else ""
            parts = [f"{w}W", f"{l}L"]
            if mc:
                parts.append(f"{mc}MC")
            if be:
                parts.append(f"{be}BE")
            parts.append(wr_str) if wr_str else None
            lines.append(" · ".join(parts))
            lines.append("")
    except Exception:
        pass

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
                    lines.append(f"Stop SL   <code>~${hypo_sl:,.0f}</code> (if fired)")
                    lines.append(f"Take TP   <code>~${hypo_tp:,.0f}</code> (if fired)")
                else:
                    lines.append("Stop SL   —")
                    lines.append("Take TP   —")
                lines.append(f"R/R       <b>1:{RISK_CONFIG['take_profit_rr']:.1f}</b>")
                lines.append("Position  — (no active trade)")
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
                    lines.append("Direction —")
                    lines.append("Leverage  —")
                    lines.append("Margin    —")
                    lines.append("Max Risk  —")
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
                    lines.append(f"Stop SL   <code>~${hypo_sl:,.0f}</code> (if fired)")
                    lines.append(f"Take TP   <code>~${hypo_tp:,.0f}</code> (if fired)")
                else:
                    lines.append("Stop SL   —")
                    lines.append("Take TP   —")
                lines.append(f"R/R       <b>1:{RISK_CONFIG['take_profit_rr']:.1f}</b>")
                lines.append("Direction —")
                lines.append("Leverage  —")
                lines.append("Margin    —")
                risk_amt = balance * FUTURES_CONFIG["risk_per_trade"]
                lines.append(f"Max Risk  <code>${risk_amt:,.0f}</code> ({FUTURES_CONFIG['risk_per_trade']*100:.0f}%/trade)")

        lines.append("")

    # ── NOTE ───────────────────────────────────────────────────
    lines.append("<b>━━━ 📝 NOTE ━━━</b>")
    for sig in [spot_signal, futures_signal]:
        if not sig:
            continue
        mode   = sig.get("mode", "futures")
        stype  = sig["type"]
        label  = "SPOT" if mode == "spot" else "FUTURES"
        buy_s  = sig.get("buy_score", 0)
        sell_s = sig.get("sell_score", 0)
        threshold = sig.get("_threshold", 0)

        if stype == "HOLD":
            if buy_s > sell_s:
                dir_str, lead = "BUY", buy_s
            elif sell_s > buy_s:
                dir_str = "BEARISH" if mode == "spot" else "SELL"
                lead = sell_s
            else:
                dir_str, lead = "NEUTRAL", max(buy_s, sell_s)
            gap = threshold - lead

            if dir_str == "NEUTRAL":
                lines.append(f"{label}    NEUTRAL (both {buy_s:.2f}) — gap {gap:.2f} to fire")
            elif mode == "spot" and dir_str == "BEARISH":
                lines.append(f"{label}    BEARISH leads ({buy_s:.2f} buy vs {sell_s:.2f} sell) — BUY‑only → HOLD")
            elif gap <= 0:
                lines.append(f"{label}    {dir_str} by {abs(buy_s - sell_s):.2f} — gap 0.00 READY but overrides")
            else:
                lines.append(f"{label}    {dir_str} leads ({lead:.2f}) — gap {gap:.2f} to fire")
        else:
            lines.append(f"{label}    {stype} {sig.get('strength', 0):.2f}/{_max_score(sig)} > threshold {threshold:.2f} → FIRED")

    lines.append("")
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

