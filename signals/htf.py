"""Multi-timeframe analysis — HTF indicators, trend detection for futures (4H+1D)
and spot (1D+1W) pipelines.
"""

import logging

import pandas as pd

from signals.indicators import calculate_ema, calculate_macd, calculate_rsi
from signals.market_data import exchange

logger = logging.getLogger(__name__)


def _htf_indicators(df):
    """Compute multi-timeframe indicators from OHLCV DataFrame.
    Returns dict with trend, RSI, MACD, volume trend, and price position."""
    close = df['close']
    ema200 = calculate_ema(close, 200)
    rsi14 = calculate_rsi(close, 14)
    macd, macd_sig, _ = calculate_macd(close)

    last = close.iloc[-1]
    ema_last = ema200.iloc[-1]
    trend = 'BULLISH' if last > ema_last else 'BEARISH'

    rsi_val = rsi14.iloc[-1]
    if rsi_val < 30:
        rsi_zone = 'oversold'
    elif rsi_val < 45:
        rsi_zone = 'low'
    elif rsi_val <= 55:
        rsi_zone = 'neutral'
    elif rsi_val <= 70:
        rsi_zone = 'elevated'
    else:
        rsi_zone = 'overbought'

    macd_bias = 'BULLISH' if macd.iloc[-1] > macd_sig.iloc[-1] else 'BEARISH'

    vol_avg = df['volume'].rolling(20).mean().iloc[-1]
    vol_last = df['volume'].tail(5).mean()
    if vol_last > vol_avg * 1.3:
        vol_trend = 'RISING'
    elif vol_last < vol_avg * 0.7:
        vol_trend = 'FALLING'
    else:
        vol_trend = 'FLAT'

    pct_from_ema = (last - ema_last) / ema_last * 100

    return {
        'trend': trend,
        'rsi': round(rsi_val, 1),
        'rsi_zone': rsi_zone,
        'macd': macd_bias,
        'vol_trend': vol_trend,
        'pct_ema': round(pct_from_ema, 2),
    }


def _htf_aligned(ind_fast, ind_slow, direction):
    """True if both timeframes agree on direction AND RSIs are not both in an extreme
    counter-trend zone (e.g., both overbought while bullish = impending reversal)."""
    if direction == 'BULLISH':
        # Block if BOTH timeframes are overbought — momentum exhausted
        both_ob = (ind_fast.get('rsi_zone') == 'overbought' and
                   ind_slow.get('rsi_zone') == 'overbought')
        return not both_ob
    else:
        # Block if BOTH timeframes are oversold — potential mean-reversion
        both_os = (ind_fast.get('rsi_zone') == 'oversold' and
                   ind_slow.get('rsi_zone') == 'oversold')
        return not both_os


def get_htf_trend():
    """Fetch 4H + 1D multi-timeframe analysis for futures (1H base)."""
    htf = {
        '4h': 'NEUTRAL', '1d': 'NEUTRAL', 'aligned': False,
        '4h_indicators': {}, '1d_indicators': {},
    }
    try:
        for tf, key in [('4h', '4h'), ('1d', '1d')]:
            bars = exchange.fetch_ohlcv('BTC/USDT', timeframe=tf, limit=250)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            ind = _htf_indicators(df)
            htf[key] = ind['trend']
            htf[f'{key}_indicators'] = ind
        trend_match = htf['4h'] == htf['1d']
        htf['aligned'] = trend_match and _htf_aligned(
            htf['4h_indicators'], htf['1d_indicators'], htf['1d']
        )
    except Exception as e:
        logger.warning("HTF fetch failed: %s", e)
    return htf


def get_spot_htf_trend():
    """Fetch 1D + 1W multi-timeframe analysis for spot (4H base)."""
    htf = {
        '1d': 'NEUTRAL', '1w': 'NEUTRAL', 'aligned': False,
        '1d_indicators': {}, '1w_indicators': {},
    }
    try:
        for tf, key in [('1d', '1d'), ('1w', '1w')]:
            bars = exchange.fetch_ohlcv('BTC/USDT', timeframe=tf, limit=250)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            ind = _htf_indicators(df)
            htf[key] = ind['trend']
            htf[f'{key}_indicators'] = ind
        trend_match = htf['1d'] == htf['1w']
        htf['aligned'] = trend_match and _htf_aligned(
            htf['1d_indicators'], htf['1w_indicators'], htf['1w']
        )
    except Exception as e:
        logger.warning("Spot HTF fetch failed: %s", e)
    return htf
