"""Telegram and Discord alert delivery for BUY / SELL signals."""

import logging

import signal_history as _sh
from config import (
    DISCORD_WEBHOOK_URL,
    HTTP_SESSION,
    SIGNAL_MAX_SCORE,
    SPOT_MAX_SCORE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)

_UP   = "▲"
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


# ---------------------------------------------------------------------------
# Compact section formatter (used in combined messages)
# ---------------------------------------------------------------------------

def _format_section_telegram(signal, symbol="BTC/USDT"):
    """One compact section for SPOT or FUTURES in the combined message."""
    stype  = signal["type"]
    label  = _mode_label(signal)
    score  = signal.get("strength", 0)
    mscore = _max_score(signal)

    if stype == "HOLD":
        return f"⏸ <b>HOLD · {_esc(label)}</b> — Score: {score:.1f}/{mscore}"

    icon  = "🟢" if stype == "BUY" else "🔴"
    conf  = signal.get("confidence", "")
    entry = signal["entry_price"]
    sl    = signal["stop_loss"]
    tp1   = signal["take_profit"]
    tp2   = signal.get("tp2")

    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    header = f"{icon} <b>{stype} · {_esc(label)}</b>"
    if conf:
        header += f" — <b>{conf}</b>"

    lines = [header, ""]

    lines.append("<b>📊 Trade Setup</b>")
    lines.append(f"Entry    <code>${entry:,.0f}</code>")
    lines.append(f"Trail SL <code>${sl:,.0f}</code>  (-{sl_pct:.2f}%)")
    lines.append(f"TP1 50%  <code>${tp1:,.0f}</code>  (+{tp1_pct:.2f}%)")
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        lines.append(f"TP2 50%  <code>${tp2:,.0f}</code>  (+{tp2_pct:.2f}%)")
    lines.append(f"R/R 1:{rr:.2f}  ·  Score {score:.2f}/{mscore}")
    lines.append("")

    htf  = signal.get("_htf", {})
    last = signal.get("_last", {})
    mkt  = signal.get("_market", {})

    if htf:
        htf_keys  = [k for k in htf if k != "aligned"]
        htf_parts = "  ·  ".join(f"{k.upper()}: {htf[k]}" for k in htf_keys)
        aligned   = "✓" if htf.get("aligned") else "✗"
        lines.append(f"<b>⏱ HTF</b> {_esc(htf_parts)}  {aligned}")

    if last:
        rsi_v = last.get("rsi", 0)
        sk    = last.get("stoch_k")
        macd  = last.get("macd", 0)
        msig  = last.get("macd_sig", 0)
        sk_str = f"  StochRSI <code>{sk:.0f}</code>" if sk is not None else ""
        macd_str = _UP if macd > msig else _DOWN
        rsi_tag  = "  OB" if rsi_v > 70 else ("  OS" if rsi_v < 30 else "")
        lines.append(f"RSI <code>{rsi_v:.1f}</code>{rsi_tag}{sk_str}  ·  MACD {macd_str}")

    if mkt and signal.get("mode") == "futures":
        funding  = mkt.get("funding", {})
        ls       = mkt.get("long_short", {})
        oi       = mkt.get("open_interest", {})
        fund_pct = funding.get("rate_pct", 0)
        oi_val   = oi.get("notional", 0)
        oi_dir   = _dir(oi.get("change_pct", 0))
        lines.append(
            f"Funding <code>{fund_pct:+.4f}%</code>"
            + (f"  ·  OI <code>${oi_val/1e9:.1f}B</code> {oi_dir}" if oi_val else "")
            + f"  ·  L/S <code>{ls.get('ratio',1):.2f}</code>"
        )

    if mkt:
        btcdom   = mkt.get("btc_dom", {})
        stable   = mkt.get("stablecoin", {})
        btcd     = btcdom.get("current", 0)
        btcd_dir = _dir(btcdom.get("change_pct", 0))
        stab     = stable.get("total_b", 0)
        stab_dir = _dir(stable.get("change_pct", 0))
        parts = []
        if btcd:
            parts.append(f"BTC.D <code>{btcd:.1f}%</code> {btcd_dir}")
        if stab:
            parts.append(f"Stbl <code>${stab:.0f}B</code> {stab_dir}")
        if parts:
            lines.append("  ·  ".join(parts))

    lines.append("")
    reasons = signal.get("reasons", [])
    if reasons:
        lines.append(f"<b>✅ Signals ({len(reasons)})</b>")
        for r in reasons[:5]:
            sym  = "✓" if r.startswith("✓") else ("✗" if r.startswith("✗") else "•")
            body = r[2:].strip() if r[:1] in "✓✗⚠" else r.strip()
            lines.append(f"  {sym} {_esc(body[:65])}")

    return "\n".join(lines)


def _format_section_discord(signal, symbol="BTC/USDT"):
    """Compact Discord section for one mode."""
    stype  = signal["type"]
    label  = _mode_label(signal)
    score  = signal.get("strength", 0)
    mscore = _max_score(signal)

    if stype == "HOLD":
        return f"⏸ **HOLD · {label}** — Score: {score:.1f}/{mscore}"

    icon  = "🟢" if stype == "BUY" else "🔴"
    conf  = signal.get("confidence", "")
    entry = signal["entry_price"]
    sl    = signal["stop_loss"]
    tp1   = signal["take_profit"]
    tp2   = signal.get("tp2")
    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    header = f"{icon} **{stype} · {label}**"
    if conf:
        header += f" — **{conf}**"

    trade = (
        f"```\n"
        f"Entry    ${entry:>10,.0f}\n"
        f"Trail SL ${sl:>10,.0f}  (-{sl_pct:.2f}%)\n"
        f"TP1 50%  ${tp1:>10,.0f}  (+{tp1_pct:.2f}%)\n"
    )
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        trade += f"TP2 50%  ${tp2:>10,.0f}  (+{tp2_pct:.2f}%)\n"
    trade += f"R/R  1:{rr:.2f}  ·  Score {score:.2f}/{mscore}\n```"

    htf  = signal.get("_htf", {})
    last = signal.get("_last", {})
    mkt  = signal.get("_market", {})

    parts = [header, trade]

    if htf:
        htf_keys = [k for k in htf if k != "aligned"]
        htf_str  = "  ·  ".join(f"{k.upper()}: {htf[k]}" for k in htf_keys)
        aligned  = "✓" if htf.get("aligned") else "✗"
        parts.append(f"**HTF** {htf_str} {aligned}")

    if last:
        rsi_v  = last.get("rsi", 0)
        sk     = last.get("stoch_k")
        macd   = last.get("macd", 0)
        msig_v = last.get("macd_sig", 0)
        sk_str = f" · StochRSI {sk:.0f}" if sk is not None else ""
        parts.append(
            f"**Technicals** · RSI {rsi_v:.1f}{sk_str}"
            f" · MACD {'▲' if macd > msig_v else '▼'}"
        )

    if mkt and signal.get("mode") == "futures":
        funding  = mkt.get("funding", {})
        ls       = mkt.get("long_short", {})
        fund_pct = funding.get("rate_pct", 0)
        parts.append(f"**Market** · Funding {fund_pct:+.4f}% · L/S {ls.get('ratio',1):.2f}")

    reasons = signal.get("reasons", [])
    if reasons:
        top = "\n".join(
            f"{'✓' if r.startswith('✓') else '✗'} {r[2:].strip()[:60]}"
            for r in reasons[:5]
        )
        parts.append(f"**Signals ({len(reasons)})**\n```\n{top}\n```")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Combined formatters
# ---------------------------------------------------------------------------

def _format_combined_telegram(spot_signal, futures_signal, symbol="BTC/USDT"):
    parts = []
    parts.append(_format_section_telegram(spot_signal, symbol))
    parts.append("")
    parts.append("━" * 35)
    parts.append("")
    parts.append(_format_section_telegram(futures_signal, symbol))
    parts.append("")

    # Paper performance footer
    try:
        spot_pnl, spot_cnt, _ = _sh.get_closed_pnl("spot")
        fut_pnl, fut_cnt, _   = _sh.get_closed_pnl("futures")
        perf_lines = ["<b>📉 Paper Performance</b>"]
        if spot_cnt > 0:
            perf_lines.append(f"SPOT: {spot_cnt} trades  ·  P&amp;L {_esc(f'{spot_pnl:+.2f}%')}")
        if fut_cnt > 0:
            perf_lines.append(f"FUTURES: {fut_cnt} trades  ·  P&amp;L {_esc(f'{fut_pnl:+.2f}%')}")
        if spot_cnt > 0 or fut_cnt > 0:
            parts.extend(perf_lines)
    except Exception:
        pass

    return "\n".join(parts)


def _format_combined_discord(spot_signal, futures_signal, symbol="BTC/USDT"):
    parts = []
    parts.append(_format_section_discord(spot_signal, symbol))
    parts.append("━" * 30)
    parts.append(_format_section_discord(futures_signal, symbol))

    try:
        spot_pnl, spot_cnt, _ = _sh.get_closed_pnl("spot")
        fut_pnl, fut_cnt, _   = _sh.get_closed_pnl("futures")
        if spot_cnt > 0 or fut_cnt > 0:
            parts.append("**📉 Paper Performance**")
            if spot_cnt > 0:
                parts.append(f"SPOT: {spot_cnt} trades · P&L {spot_pnl:+.2f}%")
            if fut_cnt > 0:
                parts.append(f"FUTURES: {fut_cnt} trades · P&L {fut_pnl:+.2f}%")
    except Exception:
        pass

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Full single-signal formatters (legacy / standalone use)
# ---------------------------------------------------------------------------

def _format_telegram(signal, symbol="BTC/USDT"):
    stype   = signal["type"]
    icon    = "🟢" if stype == "BUY" else "🔴"
    conf    = signal.get("confidence", "")
    mscore  = _max_score(signal)
    label   = _mode_label(signal)

    entry   = signal["entry_price"]
    sl      = signal["stop_loss"]
    tp1     = signal["take_profit"]
    tp2     = signal.get("tp2")
    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    lines = []

    header = f"{icon} <b>{stype} {_esc(symbol)} · {_esc(label)}</b>"
    if conf:
        header += f" — <b>{conf}</b>"
    lines += [header, ""]

    lines.append("<b>📊 Trade Setup</b>")
    lines.append(f"Entry:      <code>${entry:,.0f}</code>")
    lines.append(f"Trail SL:   <code>${sl:,.0f}</code>  (-{sl_pct:.2f}%)")
    lines.append(f"TP1 (50%):  <code>${tp1:,.0f}</code>  (+{tp1_pct:.2f}%)")
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        lines.append(f"TP2 (50%):  <code>${tp2:,.0f}</code>  (+{tp2_pct:.2f}%)")
    lines.append(f"R/R: 1:{rr:.2f}  ·  Score: {signal['strength']:.2f} / {mscore}")
    lines.append("")

    last = signal.get("_last", {})
    if last:
        ema200 = last.get("ema200", 0)
        hi24   = last.get("hi24", 0)
        lo24   = last.get("lo24", 0)
        trend  = _UP if entry > ema200 else _DOWN
        lines.append("<b>📈 Price &amp; Trend</b>")
        lines.append(f"Price  <code>${entry:,.0f}</code>  ·  EMA200  <code>${ema200:,.0f}</code>  {trend}")
        lines.append(f"Range  <code>${lo24:,.0f}</code> – <code>${hi24:,.0f}</code>")
        sr = signal.get("support_resistance", {})
        sr_parts = []
        if sr.get("resistance"):
            sr_parts.append(f"R <code>${sr['resistance']:,.0f}</code>")
        if sr.get("support"):
            sr_parts.append(f"S <code>${sr['support']:,.0f}</code>")
        if sr_parts:
            lines.append("  ·  ".join(sr_parts))
        lines.append("")

    htf = signal.get("_htf", {})
    if htf:
        htf_keys  = [k for k in htf if k != "aligned"]
        htf_parts = "  ·  ".join(f"{k.upper()}: {htf[k]}" for k in htf_keys)
        aligned   = "✓ Aligned" if htf.get("aligned") else "✗ Diverging"
        lines.append("<b>⏱ Timeframe</b>")
        lines.append(f"{_esc(htf_parts)}  ·  {aligned}")
        lines.append("")

    if last:
        rsi_v  = last.get("rsi", 0)
        sk     = last.get("stoch_k")
        sd     = last.get("stoch_d")
        macd   = last.get("macd", 0)
        msig   = last.get("macd_sig", 0)
        vwap   = last.get("vwap")
        obv    = last.get("obv_slope", 0)
        atr_v  = last.get("atr", 0)
        div    = signal.get("rsi_divergence", "NONE")
        div_str = div if div != "NONE" else "─"

        rsi_tag  = "  OB" if rsi_v > 70 else ("  OS" if rsi_v < 30 else "")
        macd_tag = _UP if macd > msig else _DOWN
        obv_tag  = _UP if obv > 0 else _DOWN
        sk_str   = f"  StochRSI <code>{sk:.0f} / {sd:.0f}</code>" if sk is not None else ""

        lines.append("<b>🔬 Technicals</b>")
        lines.append(f"RSI <code>{rsi_v:.1f}</code>{rsi_tag}{sk_str}")
        if vwap:
            vwap_tag = _UP if entry > vwap else _DOWN
            lines.append(f"MACD {macd_tag}  ·  VWAP <code>${vwap:,.0f}</code> {vwap_tag}  ·  OBV {obv_tag}")
        else:
            lines.append(f"MACD {macd_tag}  ·  OBV {obv_tag}")
        lines.append(f"ATR <code>${atr_v:,.0f}</code>  ·  Divergence: {_esc(div_str)}")
        lines.append("")

    mkt = signal.get("_market", {})
    if mkt:
        funding = mkt.get("funding", {})
        ls      = mkt.get("long_short", {})
        dxy     = mkt.get("dxy", {})
        sp500   = mkt.get("sp500", {})
        btcdom  = mkt.get("btc_dom", {})
        oi      = mkt.get("open_interest", {})
        stable  = mkt.get("stablecoin", {})

        lines.append("<b>🏦 Market Structure</b>")

        if signal.get("mode") == "futures":
            fund_pct = funding.get("rate_pct", 0)
            ls_ratio = ls.get("ratio", 1)
            lines.append(f"Funding <code>{fund_pct:+.4f}%</code>  ·  L/S <code>{ls_ratio:.2f}</code>")

        dxy_c = dxy.get("current", 0)
        dxy_d = dxy.get("change_pct", 0)
        sp_c  = sp500.get("current", 0)
        sp_d  = sp500.get("change_pct", 0)
        if sp_c:
            lines.append(
                f"DXY <code>{dxy_c:.1f}</code> ({dxy_d:+.2f}%)  ·  "
                f"S&amp;P <code>{sp_c:,.0f}</code> ({sp_d:+.2f}%)"
            )

        btcd     = btcdom.get("current", 0)
        btcd_dir = _dir(btcdom.get("change_pct", 0))
        stab     = stable.get("total_b", 0)
        stab_dir = _dir(stable.get("change_pct", 0))
        if btcd:
            oi_val   = oi.get("notional", 0)
            oi_dir   = _dir(oi.get("change_pct", 0))
            oi_part  = f"  ·  OI <code>${oi_val/1e9:.1f}B</code> {oi_dir}" if oi_val and signal.get("mode") == "futures" else ""
            lines.append(f"BTC.D <code>{btcd:.1f}%</code> {btcd_dir}{oi_part}")
        if stab:
            lines.append(f"Stablecoin <code>${stab:.0f}B</code> {stab_dir}")
        lines.append("")

    fng_val   = signal.get("fear_greed_value", 50)
    fng_label = signal.get("fear_greed_label", "Neutral")
    news_sent = signal.get("news_sentiment", "NEUTRAL")
    news_conf = signal.get("news_confidence", 0)
    lines.append("<b>📰 Sentiment</b>")
    lines.append(
        f"F&amp;G <code>{fng_val}/100</code> — {_esc(fng_label)}"
        f"  ·  News: {news_sent} ({news_conf:.0f}%)"
    )
    news_data = signal.get("_news_data") or {}
    for h in news_data.get("headlines", [])[:4]:
        arr  = _UP if h["sentiment"] > 0 else (_DOWN if h["sentiment"] < 0 else _FLAT)
        cat  = "G" if h.get("category") == "geopolitical" else "C"
        lines.append(f"  {arr} [{cat}] {_esc(h['title'][:65])}")
    lines.append("")

    reasons = signal.get("reasons", [])
    if reasons:
        lines.append(f"<b>✅ Active Signals ({len(reasons)})</b>")
        for r in reasons[:10]:
            sym  = "✓" if r.startswith("✓") else ("✗" if r.startswith("✗") else "•")
            body = r[2:].strip() if r[:1] in "✓✗⚠" else r.strip()
            lines.append(f"  {sym} {_esc(body[:70])}")
        lines.append("")

    try:
        mode = signal.get("mode", "futures")
        total_pnl, count, avg = _sh.get_closed_pnl(mode)
        if count > 0:
            wr  = _sh.get_win_rate()
            pf  = _sh.get_profit_factor()
            pf_str = "∞" if pf == float("inf") else (f"{pf:.2f}" if pf else "—")
            wr_str = f"{wr:.0%}" if wr is not None else "—"
            pnl_str = f"{total_pnl:+.2f}%"
            lines.append("<b>📉 Paper Performance</b>")
            lines.append(
                f"{count} trades  ·  Win {wr_str}  ·  PF {pf_str}  ·  P&amp;L {_esc(pnl_str)}"
            )
    except Exception:
        pass

    return "\n".join(lines)


def _format_discord(signal, symbol="BTC/USDT"):
    stype  = signal["type"]
    icon   = "🟢" if stype == "BUY" else "🔴"
    conf   = signal.get("confidence", "")
    mscore = _max_score(signal)
    label  = _mode_label(signal)
    entry  = signal["entry_price"]
    sl     = signal["stop_loss"]
    tp1    = signal["take_profit"]
    tp2    = signal.get("tp2")
    sl_pct  = abs(entry - sl)  / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

    parts = []
    header = f"{icon} **{stype} {symbol} · {label}**"
    if conf:
        header += f" — **{conf}**"
    parts.append(header)

    trade = (
        f"```\n"
        f"Entry    ${entry:>10,.0f}\n"
        f"Trail SL ${sl:>10,.0f}  (-{sl_pct:.2f}%)\n"
        f"TP1 50%  ${tp1:>10,.0f}  (+{tp1_pct:.2f}%)\n"
    )
    if tp2:
        tp2_pct = abs(tp2 - entry) / entry * 100
        trade += f"TP2 50%  ${tp2:>10,.0f}  (+{tp2_pct:.2f}%)\n"
    trade += f"R/R  1:{rr:.2f}  ·  Score {signal['strength']:.2f}/{mscore}\n```"
    parts.append(trade)

    htf  = signal.get("_htf", {})
    last = signal.get("_last", {})
    mkt  = signal.get("_market", {})

    if htf:
        htf_keys = [k for k in htf if k != "aligned"]
        htf_str  = " · ".join(f"{k.upper()}: {htf[k]}" for k in htf_keys)
        aligned  = "✓" if htf.get("aligned") else "✗"
        parts.append(f"**Timeframe** · {htf_str} {aligned}")

    if last:
        rsi_v  = last.get("rsi", 0)
        sk     = last.get("stoch_k")
        macd   = last.get("macd", 0)
        msig_v = last.get("macd_sig", 0)
        sk_str = f" · StochRSI {sk:.0f}" if sk is not None else ""
        parts.append(
            f"**Technicals** · RSI {rsi_v:.1f}{sk_str}"
            f" · MACD {'▲' if macd > msig_v else '▼'}"
        )

    if mkt:
        btcdom   = mkt.get("btc_dom", {})
        btcd     = btcdom.get("current", 0)
        if signal.get("mode") == "futures":
            funding  = mkt.get("funding", {})
            ls       = mkt.get("long_short", {})
            fund_pct = funding.get("rate_pct", 0)
            parts.append(
                f"**Market** · Funding {fund_pct:+.4f}% · L/S {ls.get('ratio',1):.2f}"
                + (f" · BTC.D {btcd:.1f}%" if btcd else "")
            )
        elif btcd:
            parts.append(f"**Market** · BTC.D {btcd:.1f}%")

    fng_val = signal.get("fear_greed_value", 50)
    fng_lbl = signal.get("fear_greed_label", "Neutral")
    news    = signal.get("news_sentiment", "NEUTRAL")
    parts.append(f"**Sentiment** · F&G {fng_val}/100 — {fng_lbl} · News {news}")

    reasons = signal.get("reasons", [])
    if reasons:
        top = "\n".join(
            f"{'✓' if r.startswith('✓') else '✗'} {r[2:].strip()[:65]}"
            for r in reasons[:5]
        )
        parts.append(f"**Signals ({len(reasons)})**\n```\n{top}\n```")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_telegram_alert(signal, symbol="BTC/USDT"):
    """Send a Telegram HTML-formatted message for a single BUY/SELL signal."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if signal["type"] == "HOLD":
        return

    text = _format_telegram(signal, symbol)
    try:
        resp = HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("Telegram alert sent.")
        else:
            logger.warning("Telegram returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)


def send_discord_alert(signal, symbol="BTC/USDT"):
    """Send a Discord webhook message for a single BUY/SELL signal."""
    if not DISCORD_WEBHOOK_URL:
        return
    if signal["type"] == "HOLD":
        return

    text = _format_discord(signal, symbol)
    try:
        resp = HTTP_SESSION.post(
            DISCORD_WEBHOOK_URL,
            json={"content": text},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            logger.info("Discord alert sent.")
        else:
            logger.warning("Discord returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Discord alert failed: %s", e)


def send_signal_alert(spot_signal=None, futures_signal=None, symbol="BTC/USDT"):
    """Send combined SPOT + FUTURES alert to all configured channels.

    Sends only if at least one signal is non-HOLD. Falls back to single-signal
    rich format if only one signal is provided.
    """
    # Backward compat: positional single-signal call
    if futures_signal is None and spot_signal is not None and isinstance(spot_signal, dict):
        if "mode" not in spot_signal:
            # Old-style call: send_signal_alert(signal)
            single = spot_signal
            if single["type"] != "HOLD":
                send_telegram_alert(single, symbol)
                send_discord_alert(single, symbol)
            return

    has_spot    = spot_signal is not None
    has_futures = futures_signal is not None
    spot_active    = has_spot    and spot_signal.get("type") != "HOLD"
    futures_active = has_futures and futures_signal.get("type") != "HOLD"

    if not spot_active and not futures_active:
        return  # both HOLD — silent

    if has_spot and has_futures:
        _send_combined_telegram(spot_signal, futures_signal, symbol)
        _send_combined_discord(spot_signal, futures_signal, symbol)
    elif spot_active:
        send_telegram_alert(spot_signal, symbol)
        send_discord_alert(spot_signal, symbol)
    elif futures_active:
        send_telegram_alert(futures_signal, symbol)
        send_discord_alert(futures_signal, symbol)


def _send_combined_telegram(spot_signal, futures_signal, symbol):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    text = _format_combined_telegram(spot_signal, futures_signal, symbol)
    try:
        resp = HTTP_SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("Telegram combined alert sent.")
        else:
            logger.warning("Telegram returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Telegram combined alert failed: %s", e)


def _send_combined_discord(spot_signal, futures_signal, symbol):
    if not DISCORD_WEBHOOK_URL:
        return
    text = _format_combined_discord(spot_signal, futures_signal, symbol)
    try:
        resp = HTTP_SESSION.post(
            DISCORD_WEBHOOK_URL,
            json={"content": text},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            logger.info("Discord combined alert sent.")
        else:
            logger.warning("Discord returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Discord combined alert failed: %s", e)
