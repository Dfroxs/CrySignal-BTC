"""Technical indicators — EMA, RSI, MACD, Bollinger Bands, ATR, OBV, VWAP,
Stochastic RSI, RSI divergence detection, and support/resistance detection.

All functions are pure math — no network I/O, no project dependencies.
"""

import numpy as np
import pandas as pd


def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data, period=14):
    """Wilder's RSI — exponential smoothing of avg gain/loss."""
    delta = data.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta.where(delta < 0, 0))

    # Initial values: simple average of first `period` bars
    avg_gain = gain.iloc[:period].mean()
    avg_loss = loss.iloc[:period].mean()
    result = pd.Series(index=data.index, dtype=float)

    # Wilder's smoothing: avg = (prev_avg * (period-1) + current) / period
    for i in range(len(data)):
        if i < period:
            result.iloc[i] = float('nan')
        elif i == period:
            result.iloc[i] = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100
        else:
            avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
            result.iloc[i] = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100

    return result


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
    """Wilder's ATR — exponential smoothing of True Range."""
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    # Wilder's smoothing (same as RSI): alpha = 1/period
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def calculate_obv(df):
    direction = np.sign(df['close'].diff()).fillna(0)
    return (direction * df['volume']).cumsum()


def calculate_vwap(df, period=24):
    """Rolling VWAP over N candles. period=24 on 1H data = one trading day.
    Price above VWAP = institutions net buying; below = net selling."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    return (typical_price * df['volume']).rolling(period).sum() / df['volume'].rolling(period).sum()


def compute_mfi(df, period=14):
    """Money Flow Index — volume-weighted RSI.
    Combines price direction AND volume, detecting institutional accumulation/distribution.
    Range 0-100: <20 oversold, >80 overbought."""
    typical = (df['high'] + df['low'] + df['close']) / 3
    raw_mf  = typical * df['volume']
    pos_mf  = raw_mf.where(typical > typical.shift(1), 0.0)
    neg_mf  = raw_mf.where(typical < typical.shift(1), 0.0)
    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()
    mfi = 100 - (100 / (1 + pos_sum / neg_sum.replace(0, 1e-9)))
    return mfi


def compute_cmf(df, period=20):
    """Chaikin Money Flow — accumulation/distribution oscillator.
    Range -1 to +1: >+0.10 = accumulation (buying pressure), <-0.10 = distribution."""
    hl  = (df['high'] - df['low']).replace(0, 1e-9)
    mfv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / hl * df['volume']
    vol_sum = df['volume'].rolling(period).sum().replace(0, 1e-9)
    return mfv.rolling(period).sum() / vol_sum


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
            swing_lows.append((i, lows[i], rsis[i]))   # use the actual low, not close

    swing_highs = []
    for i in range(pivot_window, n - pivot_window):
        if highs[i] == max(highs[i - pivot_window:i + pivot_window + 1]):
            swing_highs.append((i, highs[i], rsis[i]))  # use the actual high, not close

    # Dynamic threshold: ATR% of price (floor 0.2%) — scales with volatility
    atr_val = df['ATR_14'].iloc[-1] if 'ATR_14' in df.columns else 0
    price_val = df['close'].iloc[-1]
    threshold = max(0.002, atr_val / price_val) if price_val > 0 else 0.002

    if len(swing_lows) >= 2:
        i1, c1, r1 = swing_lows[-2]
        i2, c2, r2 = swing_lows[-1]
        if c2 < c1 * (1 - threshold) and r2 > r1:
            return 'BULLISH'

    if len(swing_highs) >= 2:
        i1, c1, r1 = swing_highs[-2]
        i2, c2, r2 = swing_highs[-1]
        if c2 > c1 * (1 + threshold) and r2 < r1:
            return 'BEARISH'

    return 'NONE'


def detect_support_resistance(df, lookback=50, tolerance=0.005):
    """Find nearest support/resistance from swing highs & lows.

    Returns nearest level above/below close. Among pivots within the same
    ATR-width band, the most recent pivot takes precedence.
    """
    window = df.tail(lookback).reset_index(drop=True)
    close = window['close'].iloc[-1]
    atr = window['ATR_14'].iloc[-1] if 'ATR_14' in window.columns else close * 0.005

    pivot_window = max(3, len(window) // 10)
    n = len(window)
    highs = window['high'].values
    lows  = window['low'].values

    # Collect (level, candle_index) — index is recency proxy (higher = more recent)
    swing_highs = []
    for i in range(pivot_window, n - pivot_window):
        if highs[i] == max(highs[i - pivot_window:i + pivot_window + 1]):
            swing_highs.append((highs[i], i))

    swing_lows = []
    for i in range(pivot_window, n - pivot_window):
        if lows[i] == min(lows[i - pivot_window:i + pivot_window + 1]):
            swing_lows.append((lows[i], i))

    result = {'support': None, 'resistance': None}

    # Resistance: levels above close, sorted ascending → nearest first
    r_candidates = [(h, idx) for h, idx in swing_highs if h > close * (1 + tolerance)]
    if r_candidates:
        r_candidates.sort(key=lambda x: x[0])   # nearest first
        nearest_r = r_candidates[0][0]
        # Prefer most recent pivot within 1 ATR band of the nearest level
        band = [(h, idx) for h, idx in r_candidates if h <= nearest_r + atr]
        result['resistance'] = max(band, key=lambda x: x[1])[0]  # most recent in band

    # Support: levels below close, sorted descending → nearest first
    s_candidates = [(l, idx) for l, idx in swing_lows if l < close * (1 - tolerance)]
    if s_candidates:
        s_candidates.sort(key=lambda x: x[0], reverse=True)   # nearest first
        nearest_s = s_candidates[0][0]
        band = [(l, idx) for l, idx in s_candidates if l >= nearest_s - atr]
        result['support'] = max(band, key=lambda x: x[1])[0]  # most recent in band

    return result


def detect_candlestick_pattern(df):
    """Detect bullish/bearish candlestick reversal patterns.

    Returns dict with 'bullish' and 'bearish' keys (string pattern name or None).
    Only the highest-weight pattern per direction is returned — no stacking.
    Priority: ENGULFING > MORNING/EVENING_STAR > HAMMER/SHOOTING_STAR > HARAMI.
    """
    if len(df) < 3:
        return {'bullish': None, 'bearish': None}

    c0 = df.iloc[-1]
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    def _body(c):  return abs(c['close'] - c['open'])
    def _range(c): return (c['high'] - c['low']) or 0.0001
    def _upper(c): return c['high'] - max(c['open'], c['close'])
    def _lower(c): return min(c['open'], c['close']) - c['low']
    def _bull(c):  return c['close'] > c['open']
    def _bear(c):  return c['close'] < c['open']

    bullish = None
    bearish = None

    # ── Bullish patterns ──
    if (_bear(c1) and _bull(c0) and
            c0['open'] <= c1['close'] and c0['close'] >= c1['open'] and
            _body(c0) > _body(c1)):
        bullish = 'ENGULFING'
    elif (_bear(c2) and _body(c2) >= 0.6 * _range(c2) and
          _body(c1) <= 0.3 * _range(c1) and
          _bull(c0) and _body(c0) >= 0.6 * _range(c0) and
          c0['close'] > (c2['open'] + c2['close']) / 2):
        bullish = 'MORNING_STAR'
    elif (_lower(c0) >= 2 * _body(c0) and
          _upper(c0) <= 0.1 * _range(c0) and
          0 < _body(c0) < 0.3 * _range(c0)):
        bullish = 'HAMMER'
    elif (_bear(c1) and _body(c1) >= 0.6 * _range(c1) and
          _bull(c0) and
          c0['open'] >= c1['close'] and c0['close'] <= c1['open'] and
          _body(c0) < 0.5 * _body(c1)):
        bullish = 'HARAMI'

    # ── Bearish patterns ──
    if (_bull(c1) and _bear(c0) and
            c0['open'] >= c1['close'] and c0['close'] <= c1['open'] and
            _body(c0) > _body(c1)):
        bearish = 'ENGULFING'
    elif (_bull(c2) and _body(c2) >= 0.6 * _range(c2) and
          _body(c1) <= 0.3 * _range(c1) and
          _bear(c0) and _body(c0) >= 0.6 * _range(c0) and
          c0['close'] < (c2['open'] + c2['close']) / 2):
        bearish = 'EVENING_STAR'
    elif (_upper(c0) >= 2 * _body(c0) and
          _lower(c0) <= 0.1 * _range(c0) and
          0 < _body(c0) < 0.3 * _range(c0)):
        bearish = 'SHOOTING_STAR'
    elif (_bull(c1) and _body(c1) >= 0.6 * _range(c1) and
          _bear(c0) and
          c0['open'] <= c1['close'] and c0['close'] >= c1['open'] and
          _body(c0) < 0.5 * _body(c1)):
        bearish = 'HARAMI'

    return {'bullish': bullish, 'bearish': bearish}


def compute_atr_percentile(df, lookback=100):
    """ATR percentile over lookback. 1.0 = extreme high vol, 0.0 = extreme low."""
    if 'ATR_14' not in df.columns or len(df) < lookback:
        return 0.5
    current = df['ATR_14'].iloc[-1]
    history = df['ATR_14'].iloc[-lookback:-1]
    return (history < current).sum() / len(history)


def calculate_adx(df, period=14):
    """Average Directional Index — trend strength on 0-100 scale.

    Returns DataFrame with ADX, DI+, DI- columns.
    ADX > 25 = trending, ADX < 20 = ranging, ADX 20-25 = transition.
    """
    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high.diff()
    down_move = -low.diff()  # positive when price moved down

    # True Directional Movement: +DM fires when up_move > down_move and positive
    plus_dm = up_move.where((up_move > 0) & (up_move > down_move), 0)
    minus_dm = down_move.where((down_move > 0) & (down_move > up_move), 0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_tr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_tr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_tr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()

    return pd.DataFrame({'ADX': adx, 'DI+': plus_di, 'DI-': minus_di}, index=df.index)


def classify_regime(df, adx_df=None):
    """Classify market regime: TRENDING, RANGING, or VOLATILE.

    Returns dict with regime label and adjustments.
    """
    if adx_df is None:
        adx_df = calculate_adx(df)
    adx = adx_df['ADX'].iloc[-1]
    di_plus = adx_df['DI+'].iloc[-1]
    di_minus = adx_df['DI-'].iloc[-1]
    atr_pct = compute_atr_percentile(df)

    if atr_pct > 0.90:
        regime = "VOLATILE"
        threshold_bump = 0.25   # slightly higher bar in extreme vol
        size_adj = 0.75
    elif adx > 25:
        regime = "TRENDING"
        threshold_bump = -0.25  # lower bar — trend-follow
        size_adj = 1.0
    elif adx < 20:
        regime = "RANGING"
        threshold_bump = 0.5    # raise bar — avoid whipsaws
        size_adj = 0.75
    else:
        regime = "TRANSITION"
        threshold_bump = 0.0
        size_adj = 1.0

    # Trend direction from DI+/DI-
    if di_plus > di_minus:
        trend_dir = "BULLISH"
    elif di_minus > di_plus:
        trend_dir = "BEARISH"
    else:
        trend_dir = "NEUTRAL"

    return {
        "regime": regime,
        "adx": round(adx, 1),
        "di_plus": round(di_plus, 1),
        "di_minus": round(di_minus, 1),
        "trend_dir": trend_dir,
        "threshold_bump": threshold_bump,
        "size_adj": size_adj,
    }
