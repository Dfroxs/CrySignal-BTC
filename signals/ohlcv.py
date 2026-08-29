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


_MAX_BARS_PER_CALL = 1000  # Binance caps a single fetch_ohlcv() and returns fewer silently


def _fetch_ohlcv_paged(symbol, timeframe, limit):
    """Assemble *limit* candles, paging backwards through the exchange cap.

    A single fetch_ohlcv() call returns at most _MAX_BARS_PER_CALL rows and does
    NOT signal truncation, so backtest.py asking for 2360 hourly candles silently
    got 1000 (42 days) and reported itself as a 90-day run.
    """
    if limit <= _MAX_BARS_PER_CALL:
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    bars, end = [], None
    while len(bars) < limit:
        need = min(_MAX_BARS_PER_CALL, limit - len(bars))
        since = end - need * tf_ms if end is not None else None
        chunk = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=need)
        if end is not None:
            chunk = [c for c in chunk if c[0] < end]
        if not chunk:
            break                      # exchange has no more history
        bars = chunk + bars
        end = bars[0][0]
        if len(chunk) < need:
            break
    if len(bars) < limit:
        logger.warning(
            "Requested %d %s candles, exchange only had %d", limit, timeframe, len(bars)
        )
    return bars[-limit:]


def _fetch_ohlcv_range(symbol, timeframe, since_ms, until_ms=None):
    """Page FORWARD from *since_ms* to *until_ms* inclusive.

    `_fetch_ohlcv_paged` walks backwards from now, which can only ever produce a
    window ending today. Testing one year against another needs an explicit
    span — otherwise every sample overlaps every other one and "independent
    replication" is not available at all.
    """
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    bars, cursor = [], int(since_ms)
    while True:
        chunk = exchange.fetch_ohlcv(symbol, timeframe=timeframe,
                                     since=cursor, limit=_MAX_BARS_PER_CALL)
        if bars:
            chunk = [c for c in chunk if c[0] > bars[-1][0]]
        if not chunk:
            break
        bars.extend(chunk)
        cursor = bars[-1][0] + tf_ms
        if until_ms is not None and bars[-1][0] >= until_ms:
            break
        if len(chunk) < _MAX_BARS_PER_CALL:
            break
    if until_ms is not None:
        bars = [b for b in bars if b[0] <= until_ms]
    return bars


def fetch_ohlcv_df(symbol='BTC/USDT', timeframe='1h', limit=500, vwap_period=24,
                   since=None, until=None):
    """Fetch OHLCV and attach every indicator column the engine reads.

    Pass *since* / *until* (epoch ms) for an explicit historical span; otherwise
    the most recent *limit* candles are returned.
    """
    if since is not None:
        bars = _fetch_ohlcv_range(symbol, timeframe, since, until)
    else:
        bars = _fetch_ohlcv_paged(symbol, timeframe, limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.set_index('timestamp')

    # Sanity check upstream — bad OHLCV (empty / NaN in core cols) would
    # produce nonsense indicators downstream that silently feed the engine.
    # A historical span is stale by definition — only the live path cares.
    if since is None and not _validate_ohlcv(df, timeframe):
        raise ValueError(f"OHLCV validation failed for {symbol} {timeframe}")
    if since is not None and len(df) < 10:
        raise ValueError(f"Only {len(df)} candles for {symbol} {timeframe} in that span")

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
