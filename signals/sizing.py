"""Position sizing calculations for SPOT and FUTURES modes."""

from config import FUTURES_CONFIG, LEVERAGE_CONFIG, RISK_CONFIG


def calculate_position_size(signal, account_balance=None):
    """Calculate SPOT position size based on ATR stop distance and risk config."""
    if account_balance is None:
        account_balance = RISK_CONFIG["account_balance"]

    if signal["type"] == "HOLD" or not signal.get("stop_loss"):
        return {'usdt_amount': 0, 'btc_amount': 0, 'position_ratio': 0, 'risk_amount': 0}

    entry = signal["entry_price"]
    sl = signal["stop_loss"]
    price_diff = abs(entry - sl)
    if price_diff <= 0:
        return {'usdt_amount': 0, 'btc_amount': 0, 'position_ratio': 0, 'risk_amount': 0}

    risk_pct = RISK_CONFIG["risk_per_trade"]
    risk_amount = account_balance * risk_pct
    max_position = account_balance * RISK_CONFIG["max_position_size"]

    position_size = (risk_amount / price_diff) * entry
    position_size = min(position_size, max_position)

    return {
        'usdt_amount': round(position_size, 2),
        'btc_amount': round(position_size / entry, 8) if entry > 0 else 0,
        'position_ratio': round(position_size / account_balance * 100, 1),
        'risk_amount': round(risk_amount, 2),
    }


def calculate_futures_position(signal):
    """Calculate futures position with conviction-based dynamic leverage.

    Leverage = base_risk_formula × confidence_multiplier, capped by volatility.
    Returns None when the signal is HOLD or missing stop_loss.
    """
    if signal["type"] == "HOLD" or not signal.get("stop_loss"):
        return None

    balance = FUTURES_CONFIG["futures_balance"]
    entry = signal["entry_price"]
    sl = signal["stop_loss"]
    tp = signal["take_profit"]
    max_leverage = LEVERAGE_CONFIG["base_max_leverage"]
    risk_pct = FUTURES_CONFIG["risk_per_trade"]
    max_margin_pct = FUTURES_CONFIG["max_margin_pct"]

    direction = "LONG" if signal["type"] == "BUY" else "SHORT"

    sl_distance_pct = abs(entry - sl) / entry
    if sl_distance_pct <= 0:
        return None

    # ── Volatility cap (ATR percentile) ──
    atr_pct = signal.get("_atr_percentile", 0.5)
    if atr_pct >= 0.90:
        vol_cap = 0.33
    elif atr_pct >= 0.75:
        vol_cap = 0.50
    elif atr_pct >= 0.50:
        vol_cap = 0.75
    else:
        vol_cap = 1.0

    # ── Confidence multiplier ──
    conf_mult = _compute_confidence(signal)

    # Effective risk and leverage cap
    effective_risk = risk_pct * conf_mult * vol_cap
    effective_max = int(max_leverage * vol_cap)

    risk_amount = balance * effective_risk
    max_margin = balance * max_margin_pct
    needed_position = risk_amount / sl_distance_pct
    optimal_leverage = int(needed_position / max_margin)
    leverage = max(1, min(optimal_leverage, effective_max))

    # ── Tier label ──
    if leverage <= 3:
        tier = "CONSERVATIVE"
    elif leverage <= 6:
        tier = "MODERATE"
    elif leverage <= 10:
        tier = "AGGRESSIVE"
    else:
        tier = "HIGH"

    margin = min(risk_amount / (sl_distance_pct * leverage), max_margin)
    position_value = margin * leverage

    liq_safety = 0.95
    if direction == "LONG":
        liquidation_price = entry * (1 - (1 / leverage) * liq_safety)
    else:
        liquidation_price = entry * (1 + (1 / leverage) * liq_safety)

    if direction == "LONG":
        pnl_at_tp = (tp - entry) / entry * position_value
        pnl_at_sl = (sl - entry) / entry * position_value
    else:
        pnl_at_tp = (entry - tp) / entry * position_value
        pnl_at_sl = (entry - sl) / entry * position_value

    return {
        'direction': direction,
        'leverage': leverage,
        'tier': tier,
        'margin': round(margin, 2),
        'position_value': round(position_value, 2),
        'btc_amount': round(position_value / entry, 8) if entry > 0 else 0,
        'entry': entry,
        'stop_loss': sl,
        'take_profit': tp,
        'liquidation_price': round(liquidation_price, 2),
        'pnl_at_tp': round(pnl_at_tp, 2),
        'pnl_at_sl': round(pnl_at_sl, 2),
        'risk_amount': round(risk_amount, 2),
        'margin_pct': round(margin / balance * 100, 1),
        # Diagnostics
        '_atr_pct': round(atr_pct, 2),
        '_vol_cap': round(vol_cap, 2),
        '_conf_mult': round(conf_mult, 2),
        '_eff_risk': round(effective_risk * 100, 1),
    }


def _compute_confidence(signal):
    """Conviction multiplier [0.25–1.5] from 6 technical factors.

    1.0 = baseline confidence. Factors adjust up (higher conviction) or down.
    """
    mult = 1.0
    stype = signal["type"]
    thr = signal.get("_threshold", 1)
    htf = signal.get("_htf", {}) or {}
    last = signal.get("_last", {}) or {}
    mkt = signal.get("_market", {}) or {}

    # Factor 1: Strength ratio vs threshold (weight ~30%)
    ratio = signal.get("strength", 0) / thr if thr > 0 else 1.0
    if ratio >= 2.0:
        mult += 0.30
    elif ratio >= 1.5:
        mult += 0.20
    elif ratio >= 1.2:
        mult += 0.10

    # Factor 2: HTF alignment (weight ~25%)
    if htf.get("aligned"):
        mult += 0.20
    else:
        mult -= 0.10

    # Factor 3: RSI zone confirmation (weight ~15%)
    rsi = last.get("rsi", 50)
    if stype == "BUY" and rsi < 30:
        mult += 0.15  # oversold bounce
    elif stype == "BUY" and rsi < 50:
        mult += 0.05  # healthy buy zone
    elif stype == "SELL" and rsi > 70:
        mult += 0.10  # overbought reversal (weaker)
    elif stype == "SELL" and rsi > 55:
        mult += 0.03

    # Factor 4: Funding rate (weight ~10%, BUY edge)
    funding = mkt.get("funding", {})
    if funding.get("bias") == "BULLISH" and stype == "BUY":
        mult += 0.10  # negative funding = shorts paying longs
    elif funding.get("bias") == "BEARISH" and stype == "SELL":
        mult += 0.05  # positive funding = longs crowded

    # Factor 5: Fear & Greed contrarian (weight ~10%)
    fng = signal.get("fear_greed_value", 50)
    if stype == "BUY" and fng <= 20:
        mult += 0.15  # extreme fear = contrarian buy
    elif stype == "BUY" and fng <= 40:
        mult += 0.05  # fear zone

    # Factor 6: Low volatility regime bonus (weight ~10%)
    atr_pct = signal.get("_atr_percentile", 0.5)
    if atr_pct < 0.30:
        mult += 0.10  # vol expansion expected
    elif atr_pct > 0.85:
        mult -= 0.15  # extreme vol, reduce

    return max(LEVERAGE_CONFIG["confidence_mult_min"],
               min(mult, LEVERAGE_CONFIG["confidence_mult_max"]))
