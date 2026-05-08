"""Technical indicators — EMA, RSI, MACD, Bollinger Bands, ATR, OBV, VWAP,
Stochastic RSI, RSI divergence detection, and support/resistance detection.

All functions are pure math — no network I/O, no project dependencies.
"""

import numpy as np
import pandas as pd


def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data, period=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(data, fast=12, slow=26, signal=9):
    ema_fast = data.ewm(span=fast, adjust=False).mean()
    ema_slow = data.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def calculate_bollinger_bands(data, period=20, std_dev=2):
    middle = data.rolling(window=period).mean()
    std = data.rolling(window=period).std()
    return middle + (std * std_dev), middle, middle - (std * std_dev)


def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def calculate_obv(df):
    direction = np.sign(df['close'].diff()).fillna(0)
    return (direction * df['volume']).cumsum()


def calculate_vwap(df, period=24):
    """Rolling VWAP over N candles. period=24 on 1H data = one trading day.
    Price above VWAP = institutions net buying; below = net selling."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    return (typical_price * df['volume']).rolling(period).sum() / df['volume'].rolling(period).sum()


def calculate_stoch_rsi(data, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """Stochastic RSI — K and D lines, range 0-100.
    Crossover in oversold (<20) or overbought (>80) zones gives momentum signal."""
    rsi = calculate_rsi(data, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    stoch_k_raw = 100 * (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10)
    k = stoch_k_raw.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return k, d


def detect_rsi_divergence(df, pivot_window=3, lookback=50):
    """Detect RSI divergence using swing pivot points over *lookback* candles.

    Bullish: price prints a lower swing-low vs the prior swing-low,
             but RSI prints a higher low — selling momentum is weakening.

    Bearish: price prints a higher swing-high vs the prior swing-high,
             but RSI prints a lower high — buying momentum is weakening.
    """
    window = df.tail(lookback).reset_index(drop=True)
    if len(window) < 15:
        return 'NONE'

    closes = window['close'].values
    rsis = window['RSI_14'].values
    highs = window['high'].values
    lows = window['low'].values
    n = len(window)

    swing_lows = []
    for i in range(pivot_window, n - pivot_window):
        if lows[i] == min(lows[i - pivot_window:i + pivot_window + 1]):
            swing_lows.append((i, closes[i], rsis[i]))

    swing_highs = []
    for i in range(pivot_window, n - pivot_window):
        if highs[i] == max(highs[i - pivot_window:i + pivot_window + 1]):
            swing_highs.append((i, closes[i], rsis[i]))

    if len(swing_lows) >= 2:
        i1, c1, r1 = swing_lows[-2]
        i2, c2, r2 = swing_lows[-1]
        threshold = 0.002
        if c2 < c1 * (1 - threshold) and r2 > r1:
            return 'BULLISH'

    if len(swing_highs) >= 2:
        i1, c1, r1 = swing_highs[-2]
        i2, c2, r2 = swing_highs[-1]
        threshold = 0.002
        if c2 > c1 * (1 + threshold) and r2 < r1:
            return 'BEARISH'

    return 'NONE'


def detect_support_resistance(df, lookback=50, tolerance=0.005):
    """Find nearest support/resistance from swing highs & lows."""
    window = df.tail(lookback)
    close = window['close'].iloc[-1]

    pivot_window = max(3, len(window) // 10)
    n = len(window)
    highs = window['high'].values
    lows = window['low'].values

    swing_highs = []
    for i in range(pivot_window, n - pivot_window):
        if highs[i] == max(highs[i - pivot_window:i + pivot_window + 1]):
            swing_highs.append(highs[i])

    swing_lows = []
    for i in range(pivot_window, n - pivot_window):
        if lows[i] == min(lows[i - pivot_window:i + pivot_window + 1]):
            swing_lows.append(lows[i])

    result = {'support': None, 'resistance': None}

    resistance_levels = sorted([h for h in swing_highs if h > close * (1 + tolerance)], reverse=True)
    if resistance_levels:
        result['resistance'] = resistance_levels[0]

    support_levels = sorted([l for l in swing_lows if l < close * (1 - tolerance)])
    if support_levels:
        result['support'] = support_levels[-1]

    return result


def compute_atr_percentile(df, lookback=100):
    """ATR percentile over lookback. 1.0 = extreme high vol, 0.0 = extreme low."""
    if 'ATR_14' not in df.columns or len(df) < lookback:
        return 0.5
    current = df['ATR_14'].iloc[-1]
    history = df['ATR_14'].iloc[-lookback:-1]
    return (history < current).sum() / len(history)
