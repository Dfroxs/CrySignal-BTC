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
)
from signals.market_data import exchange

logger = logging.getLogger(__name__)


def fetch_ohlcv_df(symbol='BTC/USDT', timeframe='1h', limit=500, vwap_period=24):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    df['EMA_200'] = calculate_ema(df['close'], 200)
    df['RSI_14'] = calculate_rsi(df['close'])
    df['MACD'], df['MACD_Signal'], df['MACD_Histogram'] = calculate_macd(df['close'])
    df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df['close'])
    df['ATR_14'] = calculate_atr(df)
    df['OBV'] = calculate_obv(df)
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stoch_rsi(df['close'])
    df['VWAP_24'] = calculate_vwap(df, period=vwap_period)

    return df
