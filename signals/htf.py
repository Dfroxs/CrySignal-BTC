"""Multi-timeframe analysis — HTF indicators, trend detection for futures (4H+1D)
and spot (1D+1W) pipelines.
"""

import logging

import numpy as np
import pandas as pd

from signals.indicators import calculate_ema, calculate_macd, calculate_rsi
from signals.market_data import exchange

logger = logging.getLogger(__name__)


def htf_indicator_series(df):
    """Per-bar HTF indicator frame: trend, RSI, MACD bias, volume trend, price
    position relative to EMA200.

    ``_htf_indicators()`` is simply the last row of this. The series form exists
    so backtest.py can read the values **as of a past candle** instead of
    resampling the base timeframe, which could not produce enough bars for a
    real EMA200 (or, on weekly, for any trend at all).
    """
    close  = df['close']
    ema200 = calculate_ema(close, 200)
    rsi14  = calculate_rsi(close, 14)
    macd, macd_sig, _ = calculate_macd(close)

    vol_ema20 = df['volume'].ewm(span=20, adjust=False).mean()
    vol_ema5  = df['volume'].ewm(span=5,  adjust=False).mean()

    rsi_zone = np.select(
        [rsi14.isna(), rsi14 < 30, rsi14 < 45, rsi14 <= 55, rsi14 <= 70],
        ['neutral', 'oversold', 'low', 'neutral', 'elevated'],
        default='overbought',
    )
    vol_trend = np.select(
        [vol_ema5 > vol_ema20 * 1.3, vol_ema5 < vol_ema20 * 0.7],
        ['RISING', 'FALLING'],
        default='FLAT',
    )

    return pd.DataFrame({
        'trend':     np.where(close > ema200, 'BULLISH', 'BEARISH'),
        'rsi':       rsi14.round(1),
        'rsi_zone':  rsi_zone,
        'macd':      np.where(macd > macd_sig, 'BULLISH', 'BEARISH'),
        'vol_trend': vol_trend,
        'pct_ema':   ((close - ema200) / ema200 * 100).round(2),
    }, index=df.index)


def indicators_from_row(row):
    """Convert one row of htf_indicator_series() into the plain-python dict the
    engine, notifiers and cycle_log JSON expect."""
    return {
        'trend':     str(row['trend']),
        'rsi':       float(row['rsi']) if pd.notna(row['rsi']) else 50.0,
        'rsi_zone':  str(row['rsi_zone']),
        'macd':      str(row['macd']),
        'vol_trend': str(row['vol_trend']),
        'pct_ema':   float(row['pct_ema']),
    }


def _htf_indicators(df):
    """Indicator snapshot for the most recent bar."""
    return indicators_from_row(htf_indicator_series(df).iloc[-1])


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
        trend_match = htf['4h'] != 'NEUTRAL' and htf['4h'] == htf['1d']
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
        trend_match = htf['1d'] != 'NEUTRAL' and htf['1d'] == htf['1w']
        htf['aligned'] = trend_match and _htf_aligned(
            htf['1d_indicators'], htf['1w_indicators'], htf['1w']
        )
    except Exception as e:
        logger.warning("Spot HTF fetch failed: %s", e)
    return htf
