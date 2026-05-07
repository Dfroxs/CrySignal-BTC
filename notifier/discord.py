"""Discord notification formatters and senders."""

import logging

from trading import history as _sh
from config import DISCORD_WEBHOOK_URL, HTTP_SESSION
from notifier.common import _dir, _max_score, _mode_label, _macro_banner

logger = logging.getLogger(__name__)
_UP = "▲"
_DOWN = "▼"
_FLAT = "─"


def _format_section_discord(signal, symbol="BTC/USDT"):
    """Full-detail Discord section for one mode."""
    stype  = signal["type"]
    label  = _mode_label(signal)
    score  = signal.get("strength", 0)
    mscore = _max_score(signal)
    conf   = signal.get("confidence", "")
    mode   = signal.get("mode", "futures")

    # Header
    if stype == "HOLD":
        header = f"⏸ **HOLD · {label}**  Score {score:.2f}/{mscore}"
    elif stype == "BUY":
        header = f"🟢 **BUY · {label}**" + (f" — **{conf}**" if conf else "") + f"  Score {score:.2f}/{mscore}"
    else:
        header = f"🔴 **SELL · {label}**" + (f" — **{conf}**" if conf else "") + f"  Score {score:.2f}/{mscore}"

    parts = [header]

    # Trade setup (non-HOLD only)
    if stype != "HOLD" and signal.get("stop_loss"):
        entry   = signal["entry_price"]
        sl      = signal["stop_loss"]
        tp1     = signal["take_profit"]
        tp2     = signal.get("tp2")
        sl_pct  = abs(entry - sl)  / entry * 100
        tp1_pct = abs(tp1 - entry) / entry * 100
        rr      = tp1_pct / sl_pct if sl_pct > 0 else 0

        trade = (
            f"```\n"
            f"Entry    ${entry:>10,.0f}\n"
            f"Trail SL ${sl:>10,.0f}  (-{sl_pct:.2f}%)\n"
            f"TP1 50%  ${tp1:>10,.0f}  (+{tp1_pct:.2f}%)\n"
        )
        if tp2:
            tp2_pct = abs(tp2 - entry) / entry * 100
            trade += f"TP2 50%  ${tp2:>10,.0f}  (+{tp2_pct:.2f}%)\n"
        trade += f"R/R  1:{rr:.2f}\n```"
        parts.append(trade)

    last  = signal.get("_last", {})
    price = signal.get("entry_price", last.get("close", 0))

    # Price & Trend
    if last:
        ema200 = last.get("ema200", 0)
        hi24   = last.get("hi24", 0)
        lo24   = last.get("lo24", 0)
        trend  = _UP if price > ema200 else _DOWN
        parts.append(
            f"**Price** ${price:,.0f}  EMA200 ${ema200:,.0f} {trend}"
            f"  Range ${lo24:,.0f}–${hi24:,.0f}"
        )

    # HTF
    htf = signal.get("_htf", {})
    if htf:
        htf_keys = [k for k in htf if k != "aligned"]
        htf_str  = "  ·  ".join(f"{k.upper()}: {htf[k]}" for k in htf_keys)
        aligned  = "✓ Aligned" if htf.get("aligned") else "✗ Diverging"
        parts.append(f"**HTF** {htf_str}  {aligned}")

    # Technicals
    if last:
        rsi_v  = last.get("rsi", 0)
        sk     = last.get("stoch_k")
        sd     = last.get("stoch_d")
        macd   = last.get("macd", 0)
        msig_v = last.get("macd_sig", 0)
        vwap   = last.get("vwap")
        obv    = last.get("obv_slope", 0)
        atr_v  = last.get("atr", 0)
        rsi_tag = " OB" if rsi_v > 70 else (" OS" if rsi_v < 30 else "")
        sk_str  = f"  StochRSI {sk:.0f}/{sd:.0f}" if sk is not None else ""
        tech = f"**Technicals** RSI {rsi_v:.1f}{rsi_tag}{sk_str}  MACD {'▲' if macd > msig_v else '▼'}  OBV {'▲' if obv > 0 else '▼'}"
        if vwap:
            tech += f"  VWAP ${vwap:,.0f}  ATR ${atr_v:,.0f}"
        parts.append(tech)

    # Market
    mkt = signal.get("_market", {})
    if mkt:
        mkt_lines = ["**Market**"]
        if mode == "futures":
            funding  = mkt.get("funding", {})
            ls       = mkt.get("long_short", {})
            oi       = mkt.get("open_interest", {})
            fund_pct = funding.get("rate_pct", 0)
            oi_val   = oi.get("notional", 0)
            oi_dir   = _dir(oi.get("change_pct", 0))
            mkt_lines.append(
                f"Funding {fund_pct:+.5f}%  L/S {ls.get('ratio',1):.2f}"
                + (f"  OI ${oi_val/1e9:.2f}B {oi_dir}" if oi_val else "")
            )
        dxy    = mkt.get("dxy", {})
        sp500  = mkt.get("sp500", {})
        btcdom = mkt.get("btc_dom", {})
        stable = mkt.get("stablecoin", {})
        sp_c   = sp500.get("current", 0)
        if sp_c:
            mkt_lines.append(
                f"DXY {dxy.get('current',0):.3f} ({dxy.get('change_pct',0):+.2f}%)"
                f"  S&P {sp_c:,.0f} ({sp500.get('change_pct',0):+.2f}%)"
            )
        mkt_row = []
        btcd = btcdom.get("current", 0)
        stab = stable.get("total_b", 0)
        if btcd:
            mkt_row.append(f"BTC.D {btcd:.1f}% {_dir(btcdom.get('change_pct',0))}")
        if stab:
            mkt_row.append(f"Stablecoin ${stab:.0f}B {_dir(stable.get('change_pct',0))}")
        if mkt_row:
            mkt_lines.append("  ·  ".join(mkt_row))
        parts.append("\n".join(mkt_lines))

    # Sentiment
    fng_val = signal.get("fear_greed_value", 50)
    fng_lbl = signal.get("fear_greed_label", "Neutral")
    news    = signal.get("news_sentiment", "NEUTRAL")
    parts.append(f"**Sentiment** F&G {fng_val}/100 {fng_lbl}  News: {news}")

    news_data = signal.get("_news_data") or {}
    for h in news_data.get("headlines", [])[:3]:
        arr = _UP if h["sentiment"] > 0 else (_DOWN if h["sentiment"] < 0 else _FLAT)
        cat = "G" if h.get("category") == "geopolitical" else "C"
        parts.append(f"  {arr}[{cat}] {h['title'][:60]}")

    # Reasons
    reasons = signal.get("reasons", [])
    if reasons:
        top = "\n".join(
            f"{'✓' if r.startswith('✓') else ('✗' if r.startswith('✗') else '•')} {r[2:].strip()[:65]}"
            for r in reasons[:10]
        )
        parts.append(f"**Reasons ({len(reasons)})**\n```\n{top}\n```")

    return "\n".join(parts)
def _format_position_status_discord(mode):
    """Return a short text summary of open positions for Discord."""
    positions = _sh.get_open_positions(mode)
    if not positions:
        return None

    lines = ["**📝 Open Positions**"]
    for pos in positions:
        icon    = "🟢" if pos["type"] == "BUY" else "🔴"
        entry   = pos["entry_price"]
        trail   = pos.get("trailing_stop") or pos["stop_loss"]
        tp1     = pos.get("tp1") or pos["take_profit"]
        partial = pos.get("partial_closed", 0)
        pct     = abs(entry - tp1) / entry * 100 if entry > 0 else 0
        tag     = " [½]" if partial else ""
        lines.append(
            f"{icon} {pos['type']}{tag}  "
            f"Entry ${entry:,.0f}  Trail ${trail:,.0f}  "
            f"TP1 ${tp1:,.0f} (+{pct:.1f}%)"
        )
    return "\n".join(lines)

def _outcome_line():
    """Return a one-line outcome summary: W / L / MACRO / BE."""
    bd = _sh.get_outcome_breakdown()
    if not bd:
        return None
    w = bd.get("WIN", 0)
    l = bd.get("LOSS", 0)
    m = bd.get("MACRO_CLOSE", 0)
    be = bd.get("BREAKEVEN", 0)
    parts = [f"{w}W", f"{l}L"]
    if m:
        parts.append(f"{m}MC")
    if be:
        parts.append(f"{be}BE")
    return " · ".join(parts) + f"  WR: {w/(w+l)*100:.0f}%" if (w + l) > 0 else " · ".join(parts)
def _format_combined_discord(spot_signal, futures_signal, symbol="BTC/USDT"):
    parts = []

    macro_warn = _macro_banner(spot_signal) or _macro_banner(futures_signal)
    if macro_warn:
        parts.append("⚠️ **MACRO RISK: High-impact event approaching — position may be force-closed**")
        parts.append("")

    parts.append(_format_section_discord(spot_signal, symbol))
    parts.append("━" * 30)
    parts.append(_format_section_discord(futures_signal, symbol))

    spot_pos = _format_position_status_discord("spot")
    fut_pos  = _format_position_status_discord("futures")
    if spot_pos or fut_pos:
        if spot_pos:
            parts.append("\n" + spot_pos)
        if fut_pos:
            parts.append((f"\n{fut_pos}" if not spot_pos else fut_pos))
        parts.append("")

    try:
        spot_pnl, spot_cnt, _ = _sh.get_closed_pnl("spot")
        fut_pnl, fut_cnt, _   = _sh.get_closed_pnl("futures")
        if spot_cnt > 0 or fut_cnt > 0:
            parts.append("**📉 Paper Performance**")
            outcome = _outcome_line()
            if outcome:
                parts.append(outcome)
            if spot_cnt > 0:
                parts.append(f"SPOT: {spot_cnt} trades · P&L {spot_pnl:+.2f}%")
            if fut_cnt > 0:
                parts.append(f"FUTURES: {fut_cnt} trades · P&L {fut_pnl:+.2f}%")
    except Exception:
        pass

    return "\n".join(parts)

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
