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

# Bar durations in ms. Explicit rather than parsed, so an unrecognised timeframe raises
# instead of being guessed at — a wrong duration drops the wrong row, or none, silently.
_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}


def drop_unclosed(df, timeframe, now_ms=None):
    """Remove the bar that is still forming, so the engine never scores a partial one.

    The exchange serves the current, incomplete bar as the last row and
    `engine.generate_signals` scores `df.iloc[-1]`. The bot runs at :01, one minute after
    a bar closes — so the row being scored was a bar ONE MINUTE OLD, with open, high, low
    and close within a few dollars of each other. Every rolling indicator ended on that
    stub, and the entry-wick gate divided by a range of a few dollars, measuring the first
    minute's noise rather than a rejection.

    Measured over 1,200 candles: scoring it rather than the bar that closed changes the
    verdict on 13.6% of them, and the score by up to 3.75 of SPOT_MAX_SCORE 22.50. It also
    left `backtest.py` structurally unable to reproduce the live bot, since the backtest
    scores closed bars — so no backtest figure described the system that was running.
    See docs/superpowers/specs/2026-09-24-live-vs-backtest.md.

    Running at :01 means the newest CLOSED bar is one minute old, so its close is the live
    price for every purpose here; nothing needs to be injected to replace it.

    A frame of one row is returned untouched: the engine indexes iloc[-1] and iloc[-2],
    and handing it an empty frame turns a stale-data problem into a crash.
    """
    if timeframe not in _TF_MS:
        raise ValueError(f"unknown timeframe {timeframe!r}; known: {sorted(_TF_MS)}")
    if df is None or len(df) < 2:
        return df
    tf_ms = _TF_MS[timeframe]
    if now_ms is None:
        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    last_open_ms = int(df.index[-1].value // 1_000_000)
    if now_ms < last_open_ms + tf_ms:
        return df.iloc[:-1]
    return df


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
                   since=None, until=None, closed_only=True):
    """Fetch OHLCV and attach every indicator column the engine reads.

    Pass *since* / *until* (epoch ms) for an explicit historical span; otherwise
    the most recent *limit* candles are returned.

    *closed_only* (the default) drops the bar still forming before any indicator is
    computed — see `drop_unclosed`. It defaults on because every caller wants it: the live
    paths must not score a one-minute-old stub, and `backtest.py` and `scripts/` are
    already written around closed bars. Pass False only to inspect the live partial bar.
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

    # Before the indicators, not after: the whole point is that no rolling window ends on
    # a partial bar.
    if closed_only:
        df = drop_unclosed(df, timeframe)

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
