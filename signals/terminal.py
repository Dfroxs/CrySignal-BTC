"""Terminal display — colour-coded, box-drawn signal analysis output."""

from datetime import datetime

from config import (
    FUTURES_CONFIG,
    RISK_CONFIG,
    SIGNAL_MAX_SCORE,
    SIGNAL_THRESHOLD,
    SPOT_MAX_SCORE,
    SPOT_THRESHOLD,
)
from signals.market_data import get_adaptive_threshold, get_spot_adaptive_threshold
from signals.sizing import calculate_futures_position, calculate_position_size

# ANSI colour codes
_C = {
    "rst":  "\033[0m",
    "bld":  "\033[1m",
    "dim":  "\033[2m",
    "grn":  "\033[92m",
    "red":  "\033[91m",
    "yel":  "\033[93m",
    "cyn":  "\033[96m",
    "wht":  "\033[97m",
    "gry":  "\033[90m",
}

_M = 92  # total output width


def _h(heading, width=_M):
    print(f"\n  {_C['cyn']}{_C['bld']}{heading}{_C['rst']}")


def _kv(k, v, kw=14):
    if isinstance(v, tuple):
        v, ck = v
        colour = _C.get(ck, "")
        print(f"  {_C['dim']}{k:<{kw}}{_C['rst']} {colour}{v}{_C['rst']}")
    else:
        print(f"  {_C['dim']}{k:<{kw}}{_C['rst']} {v}")


def _bias_icon(bias):
    if bias == "BULLISH":  return f"{_C['grn']}▲{_C['rst']}"
    if bias == "BEARISH":  return f"{_C['red']}▼{_C['rst']}"
    return f"{_C['gry']}─{_C['rst']}"


def _bias_stars(bias):
    if bias == "BULLISH":  return f"{_C['grn']}★★★{_C['rst']}"
    if bias == "BEARISH":  return f"{_C['red']}★★★{_C['rst']}"
    return "───"


def _colour_for(value, green_above=0, red_below=0):
    if value > green_above: return "grn"
    if value < red_below:   return "red"
    return ""


def _signal_box(signal, effective_threshold, max_score=None):
    if max_score is None:
        max_score = SPOT_MAX_SCORE if signal.get('mode') == 'spot' else SIGNAL_MAX_SCORE
    stype  = signal["type"]
    buy_s  = signal.get("buy_score", 0)
    sell_s = signal.get("sell_score", 0)
    conf   = signal.get("confidence", "")

    if stype == "HOLD":
        if buy_s > sell_s:
            dir_str, lead = "BUY", buy_s
        elif sell_s > buy_s:
            dir_str = "BEARISH" if signal.get("mode") == "spot" else "SELL"
            lead = sell_s
        else:
            dir_str, lead = "NEUTRAL", max(buy_s, sell_s)
        gap = effective_threshold - lead

        icon = "❄️"
        c1   = "yel"
        l1   = f"  {icon} {_C[c1]}{_C['bld']}HOLD{_C['rst']}"
        l1  += f"  ·  Score {_C['bld']}{signal['strength']:.2f}/{max_score}{_C['rst']}"
        l1  += f"  ·  B:{_C['grn']}{buy_s:.2f}{_C['rst']} S:{_C['red']}{sell_s:.2f}{_C['rst']}"
        if signal.get("mode") == "spot" and dir_str == "BEARISH":
            l1 += f"  ·  {_C['red']}BEARISH · BUY-only → HOLD{_C['rst']}"
        elif gap < 0:
            l1 += f"  ·  {_C['yel']}news downgrade → HOLD{_C['rst']}"
        else:
            l1 += f"  ·  gap {_C['yel']}{gap:.2f}{_C['rst']} to fire"
    else:
        icon = "🟢" if stype == "BUY" else "🔴"
        c1   = "grn" if stype == "BUY" else "red"
        l1   = f"  {icon} {_C[c1]}{_C['bld']}{stype} 🔥{_C['rst']}"
        l1  += f"  ·  Score {_C['bld']}{signal['strength']:.2f}/{max_score}{_C['rst']}"
        l1  += f"  ·  B:{_C['grn']}{buy_s:.2f}{_C['rst']} S:{_C['red']}{sell_s:.2f}{_C['rst']}"
        l1  += f"  ·  ≥ thr {_C['dim']}{effective_threshold:.2f}{_C['rst']}"

    print(f"╭{'─' * (_M - 2)}╮")
    print(f"│{l1:<{_M}}│")
    print(f"╰{'─' * (_M - 2)}╯")


def display_analysis(df, signal, news_data, htf=None, market_structure=None, timeframe='1H', mode='futures'):
    last = df.iloc[-1]
    sr = signal.get("support_resistance", {})
    effective_threshold = (
        get_spot_adaptive_threshold() if mode == 'spot' else get_adaptive_threshold()
    )

    # ── header ──
    print(f"\n{_C['dim']}╭{'─' * (_M - 2)}╮{_C['rst']}")
    mode_label = 'SPOT' if mode == 'spot' else 'FUTURES'
    title = f"SpotSignal · BTC/USDT · {timeframe} · {mode_label}"
    time_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"{_C['dim']}│{_C['rst']} {_C['bld']}{_C['wht']}{title:^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}│{_C['rst']} {_C['gry']}{time_str:^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}│{_C['rst']} {_C['gry']}{'hobby · study · experiment — not financial advice · have fun':^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}╰{'─' * (_M - 2)}╯{_C['rst']}")

    # ── signal verdict ──
    max_score = SPOT_MAX_SCORE if mode == 'spot' else SIGNAL_MAX_SCORE
    base_threshold = SPOT_THRESHOLD if mode == 'spot' else SIGNAL_THRESHOLD
    _signal_box(signal, effective_threshold, max_score=max_score)

    # ── PRICE & TREND ──
    col_w = _M - 6
    sep = f"  {'─' * col_w}"
    trend = f"{_C['grn']}▲ BULLISH{_C['rst']}" if last["close"] > last["EMA_200"] else f"{_C['red']}▼ BEARISH{_C['rst']}"

    print()
    print(f"  {_C['bld']}PRICE & TREND{_C['rst']}")
    print(sep)
    hihi = df["high"].tail(24).max() if mode != 'spot' else df["high"].tail(6).max()
    lolo = df["low"].tail(24).min() if mode != 'spot' else df["low"].tail(6).min()
    _kv("Price",        f"${last['close']:,.0f}")
    _kv("EMA 200",      f"${last['EMA_200']:,.0f}  {trend}")
    _kv("24h High",     f"${hihi:,.0f}")
    _kv("24h Low",      f"${lolo:,.0f}")
    if sr.get("resistance"):
        _kv("Resistance", f"${sr['resistance']:,.0f}")
    if sr.get("support"):
        _kv("Support",    f"${sr['support']:,.0f}")

    # ── MULTI-TIMEFRAME ──
    if htf:
        print()
        print(f"  {_C['bld']}MULTI-TIMEFRAME{_C['rst']}")
        print(sep)
        a = f"{_C['grn']}✓ ALIGNED{_C['rst']}" if htf["aligned"] else f"{_C['red']}✗ DIVERGING{_C['rst']}"
        for k in [k for k in htf if k != 'aligned' and not k.endswith('_indicators')]:
            ind = htf.get(f'{k}_indicators', {})
            rsi_v = ind.get('rsi', 0)
            macd_v = ind.get('macd', '')
            vol_v = ind.get('vol_trend', '')
            rsi_str = f"{_C['grn']}OS{_C['rst']}" if rsi_v < 30 else (f"{_C['red']}OB{_C['rst']}" if rsi_v > 70 else f"{rsi_v:.0f}")
            macd_str = f"{_C['grn']}▲{_C['rst']}" if macd_v == 'BULLISH' else (f"{_C['red']}▼{_C['rst']}" if macd_v == 'BEARISH' else "─")
            vol_str = f"{_C['grn']}▲{_C['rst']}" if vol_v == 'RISING' else (f"{_C['red']}▼{_C['rst']}" if vol_v == 'FALLING' else "─")
            detail = f"{htf[k]}  RSI {rsi_str}  MACD {macd_str}  Vol {vol_str}"
            _kv(k.upper(), detail)
        _kv("Alignment", a)

    # ── MARKET STRUCTURE ──
    if market_structure:
        funding = market_structure.get("funding", {})
        dxy = market_structure.get("dxy", {})

        print()
        print(f"  {_C['bld']}MARKET STRUCTURE{_C['rst']}")
        print(sep)

        if mode == 'futures':
            ls = market_structure.get("long_short", {})
            _kv("Funding",
                f"{funding.get('rate_pct',0):+.5f}%  {_bias_icon(funding.get('bias',''))}")
            _kv("L/S Ratio",
                f"{ls.get('ratio',1):.2f}       {_bias_icon(ls.get('bias',''))}")

        _kv("DXY",
            f"{dxy.get('current',0):.3f}  ({dxy.get('change_pct',0):+.2f}%)")
        sp500 = market_structure.get("sp500", {})
        if sp500.get("current"):
            _kv("S&P 500",
                f"{sp500['current']:,.0f}  ({sp500['change_pct']:+.2f}%)  {_bias_icon(sp500.get('bias',''))}")
        stable = market_structure.get("stablecoin", {})
        if stable.get("total_b"):
            _kv("Stablecoin",
                f"${stable['total_b']:.0f}B  {_bias_icon(stable.get('bias',''))}")
        btc_dom = market_structure.get("btc_dom", {})
        if btc_dom.get("current"):
            _kv("BTC Dominance",
                f"{btc_dom['current']:.1f}%  {_bias_icon(btc_dom.get('bias',''))}")

        if mode == 'futures':
            oi = market_structure.get("open_interest", {})
            if oi.get("notional"):
                _kv("Open Interest",
                    f"${oi['notional']/1e9:.2f}B  ({oi['change_pct']:+.3f}%)  {_bias_icon(oi.get('bias',''))}")
            basis_pct = funding.get("basis_pct", 0)
            _kv("Futures Basis",
                f"{basis_pct:+.4f}%  {_bias_icon(funding.get('basis_bias','NEUTRAL'))}")

    # ── TECHNICALS ──
    print()
    print(f"  {_C['bld']}TECHNICALS{_C['rst']}")
    print(sep)
    rsi_val = last["RSI_14"]
    rsi_tag = f" {_C['red']}OB{_C['rst']}" if rsi_val > 70 else (f" {_C['grn']}OS{_C['rst']}" if rsi_val < 30 else "")
    _kv("RSI 14", f"{rsi_val:.1f}{rsi_tag}")
    macd_tag = f"{_C['grn']}▲{_C['rst']}" if last["MACD"] > last["MACD_Signal"] else f"{_C['red']}▼{_C['rst']}"
    _kv("MACD",  f"{last['MACD']:,.0f}  {macd_tag}")
    sk = last.get("StochRSI_K")
    import pandas as pd
    if sk is not None and not pd.isna(sk):
        sd = last.get("StochRSI_D", 0)
        sk_tag = f" {_C['red']}OB{_C['rst']}" if sk > 80 else (f" {_C['grn']}OS{_C['rst']}" if sk < 20 else "")
        _kv("StochRSI K/D", f"{sk:.1f} / {sd:.1f}{sk_tag}")
    vwap_v = last.get("VWAP_24")
    if vwap_v and not pd.isna(vwap_v):
        vw_tag = f"{_C['grn']}▲{_C['rst']}" if last["close"] > vwap_v else f"{_C['red']}▼{_C['rst']}"
        _kv("VWAP 24h", f"${vwap_v:,.0f}  {vw_tag}")
    _kv("BB Upper",  f"${last['BB_Upper']:,.0f}")
    _kv("BB Middle", f"${last['BB_Middle']:,.0f}")
    _kv("BB Lower",  f"${last['BB_Lower']:,.0f}")
    _kv("ATR 14",    f"${last['ATR_14']:,.0f}")
    obv_slope = df["OBV"].iloc[-1] - df["OBV"].iloc[-5]
    obv_tag = f"{_C['grn']}▲{_C['rst']}" if obv_slope > 0 else f"{_C['red']}▼{_C['rst']}"
    _kv("OBV (5c)", f"{obv_slope:+,.0f}  {obv_tag}")
    mfi_val = last.get("MFI_14")
    if mfi_val is not None and not pd.isna(mfi_val):
        mfi_tag = f" {_C['grn']}OS{_C['rst']}" if mfi_val <= 20 else (f" {_C['red']}OB{_C['rst']}" if mfi_val >= 80 else "")
        _kv("MFI 14", f"{mfi_val:.0f}{mfi_tag}")
    cmf_val = last.get("CMF_20")
    if cmf_val is not None and not pd.isna(cmf_val):
        cmf_col = "grn" if cmf_val > 0.10 else ("red" if cmf_val < -0.10 else "dim")
        _kv("CMF 20", f"{_C[cmf_col]}{cmf_val:+.3f}{_C['rst']}")
    div = signal.get("rsi_divergence", "NONE")
    div_str = {"BULLISH": f"{_C['grn']}▲ BULL{_C['rst']}", "BEARISH": f"{_C['red']}▼ BEAR{_C['rst']}"}.get(div, "─")
    _kv("RSI Diverg", div_str)

    # ── SENTIMENT ──
    if news_data:
        print()
        print(f"  {_C['bld']}SENTIMENT{_C['rst']}")
        print(sep)
        if news_data.get("fear_greed"):
            fng = news_data["fear_greed"]
            val = fng["value"]
            bar_f = min(val // 10, 10)
            bar = f"{_C['grn']}{'█' * bar_f}{_C['gry']}{'░' * (10 - bar_f)}{_C['rst']}"
            _kv("F&G Index", f"[{bar}] {val}/100  {fng.get('label','')}")
        sent = signal.get("news_sentiment", "NEUTRAL")
        conf = signal.get("news_confidence", 0)
        sent_col = "grn" if sent == "BULLISH" else ("red" if sent == "BEARISH" else "dim")
        _kv("Sentiment", f"{_C[sent_col]}{sent}{_C['rst']}  ({conf:.0f}% confidence)")
        sources = ", ".join(news_data.get("sources_checked", []))
        _kv("Sources", f"{_C['dim']}{sources[:70]}{_C['rst']}")

        headlines = news_data.get("headlines", [])
        if headlines:
            print()
            print(f"  {_C['bld']}TOP HEADLINES{_C['rst']}")
            print(sep)
            for i, h in enumerate(headlines[:6], 1):
                icon = f"{_C['grn']}▲{_C['rst']}" if h["sentiment"] > 0 else (f"{_C['red']}▼{_C['rst']}" if h["sentiment"] < 0 else "─")
                cat = "G" if h.get("category") == "geopolitical" else "C"
                print(f"  {i}. {cat} {icon} {h['title'][:75]}")

    # ── SIGNAL REASONS ──
    reasons = signal.get("reasons", [])
    if reasons:
        print()
        print(f"  {_C['bld']}SIGNAL REASONS{_C['rst']} ({len(reasons)} active)")
        print(sep)

        groups = {"trend": [], "momentum": [], "volume": [], "structure": [], "macro": [], "other": []}
        for r in reasons:
            r_lower = r.lower()
            if any(w in r_lower for w in ("ema", "htf", "vwap", "support", "resistance")):
                groups["trend"].append(r)
            elif any(w in r_lower for w in ("rsi", "macd", "stoch", "divergence", "bollinger", "band")):
                groups["momentum"].append(r)
            elif any(w in r_lower for w in ("volume", "obv")):
                groups["volume"].append(r)
            elif any(w in r_lower for w in ("funding", "l/s", "dxy", "basis", "open interest", "liquidat")):
                groups["structure"].append(r)
            elif any(w in r_lower for w in ("s&p", "stablecoin", "dominance", "macro", "forced hold")):
                groups["macro"].append(r)
            else:
                groups["other"].append(r)

        for cat, label in [("trend", "TREND"), ("momentum", "MOMENTUM"),
                            ("volume", "VOLUME"), ("structure", "STRUCTURE"),
                            ("macro", "MACRO"), ("other", "OTHER")]:
            if groups[cat]:
                print(f"  {_C['bld']}{label}{_C['rst']}")
                for item in groups[cat]:
                    stripped = item[2:] if item[:1] in "✓✗⚠" else item[1:] if item[:1] in "─→" else item
                    icon = "✓" if item.startswith("✓") else ("✗" if item.startswith("✗") else "•")
                    icol = _C["grn"] if item.startswith("✓") else (_C["red"] if item.startswith("✗") else _C["yel"])
                    print(f"    {icol}{icon}{_C['rst']} {stripped[:70]}")

    # ── PERFORMANCE ──
    try:
        import trading.history as _sh
        wr = _sh.get_win_rate()
        pf = _sh.get_profit_factor()
        total_pnl, count, avg = _sh.get_closed_pnl()
        if count > 0:
            print()
            print(f"  {_C['bld']}PERFORMANCE{_C['rst']} (all-time paper)")
            print(sep)
            wr_str = f"{wr:.1%}" if wr is not None else "—"
            pf_str = f"{pf:.2f}" if pf is not None else "—"
            pnl_col = "grn" if total_pnl > 0 else "red"
            _kv("Trades",       str(count))
            _kv("Win Rate",     wr_str)
            _kv("Profit Factor", pf_str)
            _kv("Net P&L",      f"{_C[pnl_col]}{total_pnl:+.2f}%{_C['rst']}")
    except Exception:
        pass

    # ── POSITION SIZING (current mode only) ──
    buy_s = signal.get("buy_score", 0)
    sell_s = signal.get("sell_score", 0)
    direction = "BUY" if buy_s > sell_s else ("SELL" if sell_s > buy_s else "neutral")

    pos = calculate_position_size(signal)
    is_active = signal["type"] != "HOLD" and signal.get("stop_loss")
    futures = calculate_futures_position(signal) if is_active else None
    entry_px = signal.get("entry_price", last["close"])
    atr = last.get("ATR_14", 0)

    spot_start = RISK_CONFIG["account_balance"]
    futures_start = FUTURES_CONFIG["futures_balance"]
    try:
        spot_pnl_pct, spot_closed, _ = _sh.get_closed_pnl(mode='spot')
        futures_pnl_pct, futures_closed, _ = _sh.get_closed_pnl(mode='futures')
        closed_count = spot_closed + futures_closed
        avg_pnl = ((spot_pnl_pct + futures_pnl_pct) / closed_count) if closed_count > 0 else 0
    except Exception:
        spot_pnl_pct, futures_pnl_pct, closed_count, avg_pnl = 0, 0, 0, 0

    print()
    print(f"  {_C['bld']}POSITION SIZING{_C['rst']}")
    print(sep)
    _kv("Entry Price", f"${entry_px:,.0f}" if is_active else f"${last['close']:,.0f}")

    if mode == 'spot':
        spot_now = spot_start * (1 + spot_pnl_pct / 100)
        print(f"  {_C['bld']}SPOT{_C['rst']}  ──  {_C['dim']}Balance  ${spot_start:,.0f}{_C['rst']}", end="")
        if spot_closed > 0:
            pnl_col = "grn" if spot_pnl_pct >= 0 else "red"
            print(f"  {_C['dim']}→  now {_C[pnl_col]}${spot_now:,.0f}{_C['rst']}  {_C[('dim' if abs(spot_pnl_pct) < 1 else pnl_col)]}({spot_pnl_pct:+.1f}%){_C['rst']}")
        else:
            print()
        if is_active:
            sl = signal["stop_loss"]
            tp = signal["take_profit"]
            sl_pct = abs(entry_px - sl) / entry_px * 100
            tp_pct = abs(tp - entry_px) / entry_px * 100
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            _kv("  Stop Loss",   f"${sl:,.0f}  (-{sl_pct:.2f}%)")
            _kv("  Take Profit", f"${tp:,.0f}  (+{tp_pct:.2f}%)")
            _kv("  Risk/Reward", f"1:{rr:.2f}")
            _kv("  Position",    f"${pos['usdt_amount']:,.0f}  ({pos['position_ratio']:.1f}% of balance)")
            _kv("  Max Risk",    f"${pos['risk_amount']:,.0f}  ({RISK_CONFIG['risk_per_trade']*100:.0f}% per trade)")
        else:
            if atr > 0 and direction != "neutral":
                sl_dist = atr * RISK_CONFIG["atr_multiplier"]
                tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
                if direction == "BUY":
                    hypo_sl = last["close"] - sl_dist
                    hypo_tp = last["close"] + tp_dist
                else:
                    hypo_sl = last["close"] + sl_dist
                    hypo_tp = last["close"] - tp_dist
                _kv("  Stop Loss",   f"{_C['gry']}~${hypo_sl:,.0f} (if fired){_C['rst']}")
                _kv("  Take Profit", f"{_C['gry']}~${hypo_tp:,.0f} (if fired){_C['rst']}")
                _kv("  Risk/Reward", f"{_C['gry']}1:{RISK_CONFIG['take_profit_rr']:.1f}{_C['rst']}")
            else:
                _kv("  Stop Loss",   "-")
                _kv("  Take Profit", "-")
                _kv("  Risk/Reward", "-")
            _kv("  Position",   f"{_C['gry']}—  (no trade){_C['rst']}")
            _kv("  Max Risk",   f"${spot_start * RISK_CONFIG['risk_per_trade']:,.0f}  ({RISK_CONFIG['risk_per_trade']*100:.0f}% per trade)")

    if mode == 'futures':
        futures_now = futures_start * (1 + futures_pnl_pct / 100)
        print(f"  {_C['bld']}FUTURES{_C['rst']}  ──  {_C['dim']}Balance  ${futures_start:,.0f}{_C['rst']}", end="")
        if futures_closed > 0:
            pnl_col = "grn" if futures_pnl_pct >= 0 else "red"
            print(f"  {_C['dim']}→  now {_C[pnl_col]}${futures_now:,.0f}{_C['rst']}  {_C[('dim' if abs(futures_pnl_pct) < 1 else pnl_col)]}({futures_pnl_pct:+.1f}%){_C['rst']}")
        else:
            print()
        if is_active:
            sl = signal["stop_loss"]
            tp = signal["take_profit"]
            sl_pct = abs(entry_px - sl) / entry_px * 100
            tp_pct = abs(tp - entry_px) / entry_px * 100
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            _kv("  Stop Loss",   f"${sl:,.0f}  (-{sl_pct:.2f}%)")
            _kv("  Take Profit", f"${tp:,.0f}  (+{tp_pct:.2f}%)")
            _kv("  Risk/Reward", f"1:{rr:.2f}")
            if futures:
                _kv("  Direction",  f"{futures['direction']}")
                _kv("  Leverage",   f"{futures['leverage']}x  [{futures['tier']}]")
                _kv("  Margin",     f"${futures['margin']:,.0f}  ({futures['margin_pct']:.1f}% of balance)")
                _kv("  Pos. Value", f"${futures['position_value']:,.0f}")
                _kv("  Liquidation", f"${futures['liquidation_price']:,.0f}")
                _kv("  Max Risk",   f"${futures['risk_amount']:,.0f}  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}% per trade)")
            else:
                _kv("  Direction",  "-")
                _kv("  Leverage",   "-")
                _kv("  Margin",     "-")
                _kv("  Liquidation","-")
                _kv("  Max Risk",   "-")
        else:
            if atr > 0 and direction != "neutral":
                sl_dist = atr * RISK_CONFIG["atr_multiplier"]
                tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
                if direction == "BUY":
                    hypo_sl = last["close"] - sl_dist
                    hypo_tp = last["close"] + tp_dist
                else:
                    hypo_sl = last["close"] + sl_dist
                    hypo_tp = last["close"] - tp_dist
                _kv("  Stop Loss",   f"{_C['gry']}~${hypo_sl:,.0f} (if fired){_C['rst']}")
                _kv("  Take Profit", f"{_C['gry']}~${hypo_tp:,.0f} (if fired){_C['rst']}")
                _kv("  Risk/Reward", f"{_C['gry']}1:{RISK_CONFIG['take_profit_rr']:.1f}{_C['rst']}")
            else:
                _kv("  Stop Loss",   "-")
                _kv("  Take Profit", "-")
                _kv("  Risk/Reward", "-")
            _kv("  Direction",   "-")
            _kv("  Leverage",    "-")
            _kv("  Margin",      "-")
            _kv("  Liquidation", "-")
            _kv("  Max Risk",    f"${futures_start * FUTURES_CONFIG['risk_per_trade']:,.0f}  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}% per trade)")

    # ── VERDICT ──
    gap = effective_threshold - max(buy_s, sell_s)
    reasons_n = len(signal.get("reasons", []))

    print()
    print(f"  {_C['bld']}VERDICT{_C['rst']}")
    print(sep)

    if signal["type"] == "HOLD":
        if buy_s > sell_s:
            dir_str, lead = "BUY", buy_s
        elif sell_s > buy_s:
            dir_str = "BEARISH" if mode == "spot" else "SELL"
            lead = sell_s
        else:
            dir_str, lead = "NEUTRAL", max(buy_s, sell_s)

        if mode == "spot" and dir_str == "BEARISH":
            print(f"  ❄️ {_C['yel']}{_C['bld']}HOLD{_C['rst']}  ·  "
                  f"Score {_C['bld']}{signal['strength']:.2f}/{max_score}{_C['rst']}  ·  "
                  f"{_C['red']}BEARISH · BUY-only → HOLD{_C['rst']}")
            print(f"       B:{_C['grn']}{buy_s:.2f}{_C['rst']}  ·  "
                  f"S:{_C['red']}{sell_s:.2f}{_C['rst']}  ·  "
                  f"BUY-only → HOLD")
        else:
            print(f"  ❄️ {_C['yel']}{_C['bld']}HOLD{_C['rst']}  ·  "
                  f"Score {_C['bld']}{signal['strength']:.2f}/{max_score}{_C['rst']}  ·  "
                  f"gap {_C['yel']}{gap:.2f}{_C['rst']} to fire")
            detail = f"B:{_C['grn']}{buy_s:.2f}{_C['rst']}  ·  S:{_C['red']}{sell_s:.2f}{_C['rst']}"
            if dir_str == "NEUTRAL":
                detail += "  ·  NEUTRAL"
            elif gap <= 0:
                detail += f"  ·  {_C['yel']}{dir_str} leads · gap 0.00 READY{_C['rst']}"
            else:
                detail += f"  ·  {dir_str} leads"
            print(f"       {detail}")

        if effective_threshold != base_threshold:
            print(f"       {_C['yel']}adaptive threshold active (base={base_threshold}){_C['rst']}")
    else:
        _v_icon = "🟢" if signal["type"] == "BUY" else "🔴"
        print(f"  {_v_icon} {_C['grn' if signal['type'] == 'BUY' else 'red']}{_C['bld']}{signal['type']} 🔥{_C['rst']}  ·  "
              f"Score {_C['bld']}{signal['strength']:.2f}/{max_score}{_C['rst']}  ·  "
              f"≥ thr {_C['dim']}{effective_threshold:.2f}{_C['rst']}")
        detail = f"B:{_C['grn']}{buy_s:.2f}{_C['rst']}  ·  S:{_C['red']}{sell_s:.2f}{_C['rst']}"
        if reasons_n:
            detail += f"  ·  {reasons_n} reasons"
        if signal.get("confidence"):
            detail += f"  ·  {signal['confidence']} conf"
        print(f"       {detail}")

    try:
        _, count, avg_pnl = _sh.get_closed_pnl()
        if count > 0:
            pnl_col = "grn" if avg_pnl > 0 else "red"
            print(f"       {_C['dim']}{count} closed trades{_C['rst']}  avg {_C[pnl_col]}{avg_pnl:+.2f}%{_C['rst']}")
    except Exception:
        pass

    # ── WHAT THIS MEANS ──
    print()
    print(f"  {_C['bld']}WHAT THIS MEANS{_C['rst']}")
    print(sep)

    if signal["type"] == "BUY":
        print(f"  {_C['grn']}▶ OPEN A LONG{_C['rst']} — indicators agree on upward move.")
        print(f"  {_C['dim']}  Buy spot or open a futures long.  Use the SL and TP above.{_C['rst']}")
    elif signal["type"] == "SELL":
        print(f"  {_C['red']}▶ OPEN A SHORT{_C['rst']} — indicators agree on downward move.")
        print(f"  {_C['dim']}  Short spot or open a futures short.  Use the SL and TP above.{_C['rst']}")
    else:
        if direction == "BUY":
            print(f"  {_C['yel']}■ WAIT{_C['rst']} — bullish signals are building, but not enough to buy yet.")
        elif direction == "SELL":
            if mode == 'spot':
                print(f"  {_C['yel']}■ WAIT{_C['rst']} — bearish indicators are building, but SPOT is BUY-only. No action.")
            else:
                print(f"  {_C['yel']}■ WAIT{_C['rst']} — bearish signals are building, but not enough to sell yet.")
        else:
            print(f"  {_C['yel']}■ WAIT{_C['rst']} — market is neutral.  No clear direction.")
        print(f"  {_C['dim']}  The bot waits for strong agreement before risking capital.{_C['rst']}")
        if gap > 0:
            if mode == 'spot' and direction == 'SELL':
                print(f"  {_C['dim']}  Bearish leads by {abs(buy_s - sell_s):.2f}, but {_C['yel']}SPOT is BUY-only{_C['rst']}{_C['dim']} — no trade.{_C['rst']}")
            else:
                print(f"  {_C['dim']}  Need {_C['yel']}~{gap:.1f}{_C['dim']} more points to fire a {direction.upper()} signal.{_C['rst']}")

    # ── INDICATOR GUIDE ──
    print()
    print(f"  {_C['bld']}INDICATOR GUIDE{_C['rst']} {_C['dim']}(what each number means){_C['rst']}")
    print(sep)

    htfl = "1D and 1W" if mode == "spot" else "4H and 1D"

    guides = [
        ("Price > EMA200", "Long-term trend is up.  BTC in a bull market.",
         last["close"] > last["EMA_200"]),
        ("RSI", f"{last['RSI_14']:.0f}.  Over 70 = overbought (due for pullback).  Under 30 = oversold (bounce likely).",
         last["RSI_14"] < 70 and last["RSI_14"] > 30),
        ("MACD", "Short-term momentum.  Bullish cross = trend starting up.  Bearish = losing steam.",
         last["MACD"] > last["MACD_Signal"]),
        ("HTF Diverging", f"{htfl} charts disagree.  Market is uncertain — expect chop.",
         htf and not htf.get("aligned", True)),
    ]

    if mode == "futures":
        guides += [
            ("L/S Ratio 0.60", "More shorts than longs.  If price goes up, shorts get squeezed = fast pump.",
             True),
            ("Funding flat", "No one is paying to hold positions.  No extreme leverage either way.",
             abs(funding.get("rate_pct", 0)) < 0.01) if market_structure else ("", "", False),
        ]

    guides += [
        ("S&P 500 flat", "Equities are sideways.  BTC usually follows equities sentiment.",
         abs(sp500.get("change_pct", 0)) < 0.5) if market_structure and sp500.get("current") else ("", "", False),
        ("Fear & Greed 47", "Neutral sentiment.  Extreme fear (<20) is often a buy signal.  Extreme greed (>80) is often a sell signal.",
         news_data and news_data.get("fear_greed", {}).get("value", 50) < 60 and news_data.get("fear_greed", {}).get("value", 50) > 40),
    ]

    for label, explanation, active in guides:
        if not label:
            continue
        if active:
            print(f"  {_C['grn']}▶{_C['rst']} {_C['bld']}{label}{_C['rst']} — {_C['dim']}{explanation}{_C['rst']}")
        else:
            print(f"  {_C['gry']}·{_C['rst']} {_C['bld']}{label}{_C['rst']} — {_C['gry']}{explanation}{_C['rst']}")

    print()


# ---------------------------------------------------------------------------
# Combined SPOT + FUTURES display — narrow, fully vertical
# ---------------------------------------------------------------------------

_W = 50  # inner content width

def _mksep(char="─", width=_W):
    return f"  {_C['dim']}{char * width}{_C['rst']}"


def _section(title):
    print(f"\n  {_C['bld']}{title}{_C['rst']}")
    print(_mksep())


def _combined_box(spot_signal, futures_signal):
    """Single verdict box with both signal lines."""
    import trading.history as _sh
    box_w = _W + 2
    lines = []

    for sig, mode in [(spot_signal, "spot"), (futures_signal, "futures")]:
        if not sig:
            continue
        stype  = sig["type"]
        buy_s  = sig.get("buy_score", 0)
        sell_s = sig.get("sell_score", 0)
        score  = sig.get("strength", 0)
        max_s  = SPOT_MAX_SCORE if mode == "spot" else SIGNAL_MAX_SCORE
        thr    = sig.get("_threshold", 0)
        if mode == "spot":
            label = "SPOT 4H"
        elif stype == "BUY":
            label = "FUT LONG 1H"
        elif stype == "SELL":
            label = "FUT SHORT 1H"
        else:
            label = "FUT 1H"

        if stype == "HOLD":
            if buy_s > sell_s:
                d, lead = "BUY", buy_s
            elif sell_s > buy_s:
                d = "BEARISH" if mode == "spot" else "SELL"
                lead = sell_s
            else:
                d, lead = "NEUTRAL", max(buy_s, sell_s)
            gap = thr - lead

            if mode == "spot" and d == "BEARISH":
                lines.append(
                    f"  ❄️ {_C['yel']}{_C['bld']}{label}  HOLD{_C['rst']}  "
                    f"{score:.2f}/{max_s:.2f}  "
                    f"{_C['red']}BEARISH · BUY-only{_C['rst']}"
                )
                lines.append(
                    f"       B:{_C['grn']}{buy_s:.2f}{_C['rst']}  "
                    f"S:{_C['red']}{sell_s:.2f}{_C['rst']}  "
                    f"BUY-only → HOLD"
                )
            else:
                if gap < 0:
                    lines.append(
                        f"  ❄️ {_C['yel']}{_C['bld']}{label}  HOLD{_C['rst']}  "
                        f"{score:.2f}/{max_s:.2f}  "
                        f"{_C['yel']}news downgrade → HOLD{_C['rst']}"
                    )
                    det = (f"       B:{_C['grn']}{buy_s:.2f}{_C['rst']}  "
                           f"S:{_C['red']}{sell_s:.2f}{_C['rst']}  "
                           f"{_C['yel']}{d} leads but news blocked{_C['rst']}")
                    lines.append(det)
                else:
                    lines.append(
                        f"  ❄️ {_C['yel']}{_C['bld']}{label}  HOLD{_C['rst']}  "
                        f"{score:.2f}/{max_s:.2f}  "
                        f"gap {_C['yel']}{gap:.2f}{_C['rst']} to fire"
                    )
                    det = f"       B:{_C['grn']}{buy_s:.2f}{_C['rst']}  S:{_C['red']}{sell_s:.2f}{_C['rst']}"
                    if gap <= 0:
                        det += f"  ·  {_C['yel']}{d} leads · gap 0.00 READY{_C['rst']}"
                    elif d != "NEUTRAL":
                        det += f"  ·  {d} leads"
                    lines.append(det)
        else:
            icon = "🟢" if stype == "BUY" else "🔴"
            c1   = "grn" if stype == "BUY" else "red"
            lines.append(
                f"  {icon} {_C[c1]}{_C['bld']}{label}  {stype} 🔥{_C['rst']}  "
                f"{score:.2f}/{max_s:.2f}  "
                f"≥ thr {_C['dim']}{thr:.2f}{_C['rst']}"
            )
            det = f"       B:{_C['grn']}{buy_s:.2f}{_C['rst']}  S:{_C['red']}{sell_s:.2f}{_C['rst']}"
            reasons_n = len(sig.get("reasons", []))
            conf = sig.get("confidence", "")
            if reasons_n:
                det += f"  ·  {reasons_n} reasons"
            if conf:
                det += f"  ·  {conf} conf"
                if mode == "spot" and conf == "STRONG":
                    _existing = _sh.get_open_position_count_by_direction(stype, "spot")
                    if _existing > 0:
                        det += "  ·  🧩 pyramid eligible"
                    else:
                        det += "  ·  ⭐ entry grade: STRONG"
            lines.append(det)

    if not lines:
        return

    print(f"╭{'─' * box_w}╮")
    for l in lines:
        print(f"│{l:<{box_w + 2}}│")
    print(f"╰{'─' * box_w}╯")


def display_combined(spot_signal, futures_signal):
    """Single combined display — narrow, fully vertical, color-coded."""
    import trading.history as _sh

    primary = futures_signal or spot_signal
    if not primary:
        return
    mkt  = primary.get("_market", {}) or {}
    news = primary.get("_news_data") or {}

    # ── HEADER ──
    box_w = _W + 2
    print(f"\n{_C['dim']}╭{'─' * box_w}╮{_C['rst']}")
    time_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    print(f"{_C['dim']}│{_C['rst']} {_C['bld']}{_C['wht']}{'SpotSignal · BTC/USDT':^{box_w - 2}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}│{_C['rst']} {_C['gry']}{time_str:^{box_w - 2}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}╰{'─' * box_w}╯{_C['rst']}")

    # ── VERDICT BOX ──
    _combined_box(spot_signal, futures_signal)

    # ── MARKET STRUCTURE ──
    if mkt:
        _section("MARKET STRUCTURE")

        funding = mkt.get("funding", {})
        ls      = mkt.get("long_short", {})
        oi      = mkt.get("open_interest", {})
        dxy     = mkt.get("dxy", {})
        sp500   = mkt.get("sp500", {})
        btcdom  = mkt.get("btc_dom", {})
        stable  = mkt.get("stablecoin", {})

        if funding:
            fp = funding.get("rate_pct", 0)
            fi = _bias_icon(funding.get("bias", ""))
            _kv("Funding", f"{_C['bld']}{fp:+.5f}%{_C['rst']}  {fi}")
        if ls:
            lr = ls.get("ratio", 1)
            li = _bias_icon(ls.get("bias", ""))
            _kv("L/S", f"{_C['bld']}{lr:.2f}{_C['rst']}  {li}")
        if oi.get("notional"):
            ov = oi["notional"] / 1e9
            oi_dir = _bias_icon(oi.get("bias", ""))
            _kv("OI", f"{_C['bld']}${ov:.2f}B{_C['rst']}  {oi_dir}")
        basis_pct = funding.get("basis_pct", 0)
        if basis_pct:
            _kv("Basis", f"{_C['bld']}{basis_pct:+.4f}%{_C['rst']}")
        if dxy.get("current"):
            _kv("DXY", f"{_C['bld']}{dxy['current']:.3f}{_C['rst']}  ({dxy['change_pct']:+.2f}%)")
        if sp500.get("current"):
            sp_dir = _bias_icon(sp500.get("bias", ""))
            _kv("S&P", f"{_C['bld']}{sp500['current']:,.0f}{_C['rst']}  ({sp500['change_pct']:+.2f}%)  {sp_dir}")
        if btcdom.get("current"):
            bd_dir = _bias_icon(btcdom.get("bias", ""))
            _kv("BTC.D", f"{_C['bld']}{btcdom['current']:.1f}%{_C['rst']}  {bd_dir}")
        if stable.get("total_b"):
            st_dir = _bias_icon(stable.get("bias", ""))
            _kv("Stable", f"{_C['bld']}${stable['total_b']:.0f}B{_C['rst']}  {st_dir}")

    # ── PER-MODE SECTIONS ──
    for sig, mode in [
        (spot_signal, "spot"),
        (futures_signal, "futures"),
    ]:
        if not sig:
            continue

        stype   = sig["type"]
        last    = sig.get("_last", {})
        if not last:
            continue

        if mode == "spot":
            label = "SPOT 4H"
        elif stype == "BUY":
            label = "FUTURES LONG 1H"
        elif stype == "SELL":
            label = "FUTURES SHORT 1H"
        else:
            label = "FUTURES 1H"

        is_active = stype != "HOLD" and sig.get("stop_loss")
        entry_px  = sig.get("entry_price", last.get("close", 0))
        atr       = last.get("atr", 0)
        buy_s     = sig.get("buy_score", 0)
        sell_s    = sig.get("sell_score", 0)
        direction = "BUY" if buy_s > sell_s else ("SELL" if sell_s > buy_s else "neutral")

        _section(label)

        # Verdict line
        max_s  = SPOT_MAX_SCORE if mode == "spot" else SIGNAL_MAX_SCORE
        thr    = sig.get("_threshold", 0)
        if stype == "HOLD":
            gap = thr - max(buy_s, sell_s)
            if gap < 0:
                print(f"  ❄️ {_C['yel']}{_C['bld']}HOLD{_C['rst']}  "
                      f"Score {sig['strength']:.2f}/{max_s:.2f}  "
                      f"{_C['yel']}news downgrade → HOLD{_C['rst']}")
            else:
                print(f"  ❄️ {_C['yel']}{_C['bld']}HOLD{_C['rst']}  "
                      f"Score {sig['strength']:.2f}/{max_s:.2f}  "
                      f"gap {_C['yel']}{gap:.2f}{_C['rst']} to fire")
        else:
            icon = "🟢" if stype == "BUY" else "🔴"
            c1   = "grn" if stype == "BUY" else "red"
            print(f"  {icon} {_C[c1]}{_C['bld']}{stype} 🔥{_C['rst']}  "
                  f"Score {sig['strength']:.2f}/{max_s:.2f}  "
                  f"≥ thr {_C['dim']}{thr:.2f}{_C['rst']}")

        # Indicators (vertical)
        price  = entry_px
        ema200 = last.get("ema200", 0)
        tr_icon = f"{_C['grn']}▲ BULLISH{_C['rst']}" if price > ema200 else f"{_C['red']}▼ BEARISH{_C['rst']}"
        _kv("Price", f"${price:,.0f}")
        _kv("EMA200", f"${ema200:,.0f}  {tr_icon}")

        rsi = last.get("rsi", 0)
        rsi_tag = f" {_C['red']}OB{_C['rst']}" if rsi > 70 else (f" {_C['grn']}OS{_C['rst']}" if rsi < 30 else "")
        _kv("RSI", f"{rsi:.1f}{rsi_tag}")

        macd = last.get("macd", 0)
        msig = last.get("macd_sig", 0)
        macd_dir = f"{_C['grn']}▲{_C['rst']}" if macd > msig else f"{_C['red']}▼{_C['rst']}"
        _kv("MACD", f"{macd:+.0f}  {macd_dir}")

        sk = last.get("stoch_k")
        sd = last.get("stoch_d")
        if sk is not None:
            import pandas as pd
            if not pd.isna(sk):
                sk_tag = f" {_C['red']}OB{_C['rst']}" if sk > 80 else (f" {_C['grn']}OS{_C['rst']}" if sk < 20 else "")
                _kv("StochRSI", f"{sk:.0f} / {sd:.0f}{sk_tag}" if sd is not None else f"{sk:.0f}{sk_tag}")

        vwap = last.get("vwap")
        if vwap:
            vw_dir = f"{_C['grn']}▲{_C['rst']}" if price > vwap else f"{_C['red']}▼{_C['rst']}"
            _kv("VWAP", f"${vwap:,.0f}{vw_dir}")

        _kv("ATR", f"${atr:,.0f}")
        obv = last.get("obv_slope", 0)
        obv_dir = f"{_C['grn']}▲{_C['rst']}" if obv > 0 else f"{_C['red']}▼{_C['rst']}"
        _kv("OBV", f"{obv:+,.0f}{obv_dir}")

        mfi = last.get("mfi")
        if mfi is not None:
            import pandas as _pd
            if not _pd.isna(mfi):
                mfi_tag = f" {_C['grn']}OS{_C['rst']}" if mfi <= 20 else (f" {_C['red']}OB{_C['rst']}" if mfi >= 80 else "")
                _kv("MFI", f"{mfi:.0f}{mfi_tag}")
        cmf = last.get("cmf")
        if cmf is not None:
            if not _pd.isna(cmf):
                cmf_col = "grn" if cmf > 0.10 else ("red" if cmf < -0.10 else "dim")
                _kv("CMF", f"{_C[cmf_col]}{cmf:+.3f}{_C['rst']}")

        # HTF
        htf = sig.get("_htf", {})
        if htf:
            tf_order = ["1d", "1w"] if mode == "spot" else ["4h", "1d"]
            parts = []
            for tf in tf_order:
                if tf not in htf:
                    continue
                t = htf[tf]
                tc = "grn" if t == "BULLISH" else ("red" if t == "BEARISH" else "dim")
                parts.append(f"{tf.upper()} {_C[tc]}{t}{_C['rst']}")
            aligned = f"{_C['grn']}✓ ALIGNED{_C['rst']}" if htf.get("aligned") else f"{_C['red']}✗ DIVERGING{_C['rst']}"
            parts.append(aligned)
            _kv("HTF", "  ".join(parts))

        # S/R
        sr = sig.get("support_resistance", {})
        if sr.get("support"):
            _kv("Support", f"${sr['support']:,.0f}")
        if sr.get("resistance"):
            _kv("Resistance", f"${sr['resistance']:,.0f}")

        # RSI Divergence
        div = sig.get("rsi_divergence", "")
        if div and str(div).strip() not in ("", "NONE", "─"):
            div_str = {"BULLISH": f"{_C['grn']}▲ BULL{_C['rst']}", "BEARISH": f"{_C['red']}▼ BEAR{_C['rst']}"}.get(str(div), str(div))
            _kv("RSI Div", div_str)

        # Trade setup
        print(_mksep("─"))
        if is_active:
            sl = sig["stop_loss"]
            tp = sig["take_profit"]
            tp2 = sig.get("tp2")
            sl_pct = abs(entry_px - sl) / entry_px * 100
            tp_pct = abs(tp - entry_px) / entry_px * 100
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            _kv("SL", f"${sl:,.0f}  (-{sl_pct:.2f}%)")
            _kv("TP1", f"${tp:,.0f}  (+{tp_pct:.2f}%)")
            if tp2:
                tp2_pct = abs(tp2 - entry_px) / entry_px * 100
                _kv("TP2", f"${tp2:,.0f}  (+{tp2_pct:.2f}%)")
            _kv("R/R", f"1:{rr:.2f}")
            if mode == "spot":
                pos = calculate_position_size(sig)
                _kv("Position", f"${pos['usdt_amount']:,.0f}  ({pos['position_ratio']:.1f}%)")
                _kv("Max Risk", f"${pos['risk_amount']:,.0f}  ({RISK_CONFIG['risk_per_trade']*100:.0f}%/trade)")
            else:
                fut = calculate_futures_position(sig)
                if fut:
                    _kv("Leverage", f"{fut['leverage']}x  [{fut['tier']}]")
                    _kv("Margin", f"${fut['margin']:,.0f}  ({fut['margin_pct']:.1f}%)")
                    _kv("Liq", f"${fut['liquidation_price']:,.0f}")
                    _kv("Max Risk", f"${fut['risk_amount']:,.0f}  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}%/trade)")
        else:
            if atr > 0 and direction != "neutral":
                sl_dist = atr * RISK_CONFIG["atr_multiplier"]
                tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
                if direction == "BUY":
                    hypo_sl = entry_px - sl_dist
                    hypo_tp = entry_px + tp_dist
                else:
                    hypo_sl = entry_px + sl_dist
                    hypo_tp = entry_px - tp_dist
                _kv("If fired", f"{_C['gry']}SL ~${hypo_sl:,.0f}  TP ~${hypo_tp:,.0f}{_C['rst']}")
                _kv("R/R", f"{_C['gry']}1:{RISK_CONFIG['take_profit_rr']:.1f}{_C['rst']}")
            else:
                _kv("Status", f"{_C['gry']}No active trade{_C['rst']}")

        # Reasons (one per line)
        reasons = sig.get("reasons", [])
        if reasons:
            print(_mksep("─"))
            for r in reasons[:8]:
                if r.startswith("✓"):
                    sym, col = "✓", "grn"
                elif r.startswith("✗"):
                    sym, col = "✗", "red"
                elif r.startswith("⚠"):
                    sym, col = "⚠", "yel"
                else:
                    sym, col = "•", "dim"
                stripped = r[2:].strip()
                print(f"  {_C[col]}{sym}{_C['rst']} {stripped}")
            if len(reasons) > 8:
                print(f"  {_C['dim']}+{len(reasons) - 8} more{_C['rst']}")

    # ── SENTIMENT ──
    if news:
        _section("SENTIMENT")

        if news.get("fear_greed"):
            fng = news["fear_greed"]
            val = fng["value"]
            bar_f = min(val // 10, 10)
            bar = f"{_C['grn']}{'█' * bar_f}{_C['gry']}{'░' * (10 - bar_f)}{_C['rst']}"
            _kv("F&G", f"[{bar}] {val}/100  {fng.get('label','')}")

        primary_sig = futures_signal or spot_signal
        sent = primary_sig.get("news_sentiment", "NEUTRAL")
        conf_n = primary_sig.get("news_confidence", 0)
        sent_col = "grn" if sent == "BULLISH" else ("red" if sent == "BEARISH" else "dim")
        _kv("Sentiment", f"{_C[sent_col]}{sent}{_C['rst']}  ({conf_n:.0f}%)")

        headlines = news.get("headlines", [])
        if headlines:
            print(_mksep("─"))
            for h in headlines[:4]:
                icon = f"{_C['grn']}▲{_C['rst']}" if h["sentiment"] > 0 else (f"{_C['red']}▼{_C['rst']}" if h["sentiment"] < 0 else "─")
                print(f"  {icon} {h['title'][:55]}")

    # ── PERFORMANCE ──
    try:
        spot_pnl, sp_cnt, _ = _sh.get_closed_pnl("spot")
        fut_pnl, fu_cnt, _  = _sh.get_closed_pnl("futures")
        if sp_cnt + fu_cnt > 0:
            _section("PERFORMANCE (all-time)")

            if sp_cnt > 0:
                sp_bd = _sh.get_outcome_breakdown("spot")
                sp_w = sp_bd.get("WIN", 0)
                sp_l = sp_bd.get("LOSS", 0)
                sp_wr = f"WR {sp_w/(sp_w+sp_l)*100:.0f}%" if (sp_w + sp_l) > 0 else ""
                sp_col = "grn" if spot_pnl > 0 else "red"
                _kv("SPOT", f"{sp_cnt} trades  {sp_w}W {sp_l}L  {sp_wr}  P&L {_C[sp_col]}{spot_pnl:+.2f}%{_C['rst']}")
            if fu_cnt > 0:
                fu_bd = _sh.get_outcome_breakdown("futures")
                fu_w = fu_bd.get("WIN", 0)
                fu_l = fu_bd.get("LOSS", 0)
                fu_wr = f"WR {fu_w/(fu_w+fu_l)*100:.0f}%" if (fu_w + fu_l) > 0 else ""
                fu_col = "grn" if fut_pnl > 0 else "red"
                _kv("FUTURES", f"{fu_cnt} trades  {fu_w}W {fu_l}L  {fu_wr}  P&L {_C[fu_col]}{fut_pnl:+.2f}%{_C['rst']}")
            if sp_cnt > 0 and fu_cnt > 0:
                total_pnl = spot_pnl + fut_pnl
                tot_col = "grn" if total_pnl > 0 else "red"
                _kv("Total", f"{sp_cnt + fu_cnt} trades  P&L {_C[tot_col]}{total_pnl:+.2f}%{_C['rst']}")
    except Exception:
        pass

    print()
