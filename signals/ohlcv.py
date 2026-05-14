"""OHLCV data fetching — fetches candles from exchange, computes all indicators."""

import logging

import pandas as pd

from signals.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_obv,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_vwap,
    compute_cmf,
    compute_mfi,
)
from signals.market_data import exchange

logger = logging.getLogger(__name__)


def _validate_ohlcv(df, timeframe):
    """Validate fetched OHLCV data. Returns True if usable."""
    if df is None or len(df) < 10:
        logger.warning("OHLCV validation FAILED: empty or too few rows (%s)", len(df) if df is not None else 0)
        return False
    # Check staleness: last candle should be within 2× the interval
    import time
    tf_minutes = {"1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 60)
    last_ts = df.index[-1].timestamp() if hasattr(df.index[-1], 'timestamp') else 0
    age_minutes = (time.time() - last_ts) / 60 if last_ts > 0 else 0
    if age_minutes > tf_minutes * 2:
        logger.warning("OHLCV validation: stale data (%.0fm old, interval=%dm)", age_minutes, tf_minutes)
    # Check for NaN in critical columns
    for col in ['close', 'high', 'low', 'volume']:
        if df[col].isna().any():
            logger.warning("OHLCV validation FAILED: NaN in %s", col)
            return False
    return True


def fetch_ohlcv_df(symbol='BTC/USDT', timeframe='1h', limit=500, vwap_period=24):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.set_index('timestamp')

    # Sanity check upstream — bad OHLCV (empty / NaN in core cols) would
    # produce nonsense indicators downstream that silently feed the engine.
    if not _validate_ohlcv(df, timeframe):
        raise ValueError(f"OHLCV validation failed for {symbol} {timeframe}")

    df['EMA_200'] = calculate_ema(df['close'], 200)
    df['RSI_14'] = calculate_rsi(df['close'])
    df['MACD'], df['MACD_Signal'], df['MACD_Histogram'] = calculate_macd(df['close'])
    df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df['close'])
    df['ATR_14'] = calculate_atr(df)
    df['OBV'] = calculate_obv(df)
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stoch_rsi(df['close'])
    df['VWAP_24'] = calculate_vwap(df, period=vwap_period)
    df['MFI_14']  = compute_mfi(df)
    df['CMF_20']  = compute_cmf(df)

    return df
