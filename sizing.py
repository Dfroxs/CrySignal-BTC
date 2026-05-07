"""Position sizing calculations for SPOT and FUTURES modes."""

from config import FUTURES_CONFIG, RISK_CONFIG


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
    """Calculate futures position with leverage, margin, and liquidation price.

    Returns None when the signal is HOLD or missing stop_loss.
    """
    if signal["type"] == "HOLD" or not signal.get("stop_loss"):
        return None

    balance = FUTURES_CONFIG["futures_balance"]
    entry = signal["entry_price"]
    sl = signal["stop_loss"]
    tp = signal["take_profit"]
    max_leverage = FUTURES_CONFIG["max_leverage"]
    risk_pct = FUTURES_CONFIG["risk_per_trade"]
    max_margin_pct = FUTURES_CONFIG["max_margin_pct"]

    direction = "LONG" if signal["type"] == "BUY" else "SHORT"

    sl_distance_pct = abs(entry - sl) / entry
    if sl_distance_pct <= 0:
        return None

    optimal_leverage = int(1 / (sl_distance_pct * 100))
    leverage = max(1, min(optimal_leverage, max_leverage))

    if leverage <= 3:
        tier = "CONSERVATIVE"
    elif leverage <= 7:
        tier = "MODERATE"
    else:
        tier = "AGGRESSIVE"

    risk_amount = balance * risk_pct
    max_margin = balance * max_margin_pct
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
    }
