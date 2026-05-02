#!/usr/bin/env python3
"""
BTC/USDT Trading Signal Generator — Production Edition
=======================================================
- Volume Confirmation + OBV
- Multi-Timeframe Analysis (1H + 4H + Daily)
- Bollinger Bands
- ATR-based Dynamic Stop Loss
- Stochastic RSI (momentum crossover)
- Support/Resistance Detection (swing highs/lows)
- Fear & Greed Index
- Macro Hold Gate (HIGH impact events within 2h)
- Parallel API fetching (ThreadPoolExecutor)
- Retry with exponential backoff on all HTTP calls
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import json
import os

from config import (
    FUTURES_CONFIG,
    MACRO_CSV,
    NEWS_CSV,
    RISK_CONFIG,
    SIGNAL_HISTORY_CSV,
    SIGNAL_MAX_SCORE,
    SIGNAL_THRESHOLD,
    STABLECOIN_CACHE_FILE,
)

logger = logging.getLogger(__name__)

# ============================================================================
# HTTP SESSION — retry + exponential backoff
# ============================================================================

def _make_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retry))
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


_session  = _make_session()
exchange  = ccxt.binance()

# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data, period=14):
    delta = data.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(data, fast=12, slow=26, signal=9):
    ema_fast    = data.ewm(span=fast, adjust=False).mean()
    ema_slow    = data.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def calculate_bollinger_bands(data, period=20, std_dev=2):
    middle = data.rolling(window=period).mean()
    std    = data.rolling(window=period).std()
    return middle + (std * std_dev), middle, middle - (std * std_dev)


def calculate_atr(df, period=14):
    high_low    = df['high'] - df['low']
    high_close  = (df['high'] - df['close'].shift()).abs()
    low_close   = (df['low']  - df['close'].shift()).abs()
    true_range  = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def calculate_obv(df):
    direction = np.sign(df['close'].diff()).fillna(0)
    return (direction * df['volume']).cumsum()


def calculate_vwap(df, period=24):
    """Rolling VWAP over N candles. period=24 on 1H data ≈ one trading day.
    Price above VWAP = institutions net buying; below = net selling."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    return (typical_price * df['volume']).rolling(period).sum() / df['volume'].rolling(period).sum()


def calculate_stoch_rsi(data, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """Stochastic RSI — K and D lines, range 0–100.
    Crossover in oversold (<20) or overbought (>80) zones gives momentum signal."""
    rsi         = calculate_rsi(data, rsi_period)
    rsi_min     = rsi.rolling(stoch_period).min()
    rsi_max     = rsi.rolling(stoch_period).max()
    stoch_k_raw = 100 * (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10)
    k = stoch_k_raw.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return k, d


def detect_rsi_divergence(df, lookback=5):
    """Returns 'BULLISH', 'BEARISH', or 'NONE'."""
    # Exclude current candle so we compare it against prior history
    tail          = df.iloc[-lookback - 1:-1]
    price_min_idx = tail['close'].idxmin()
    price_max_idx = tail['close'].idxmax()
    current_close = df['close'].iloc[-1]
    current_rsi   = df['RSI_14'].iloc[-1]

    if current_close <= tail['close'].min() and current_rsi > tail['RSI_14'].loc[price_min_idx]:
        return 'BULLISH'
    if current_close >= tail['close'].max() and current_rsi < tail['RSI_14'].loc[price_max_idx]:
        return 'BEARISH'
    return 'NONE'


def detect_support_resistance(df, lookback=50, tolerance=0.005):
    """Find swing-based support and resistance from the last N candles.
    Returns nearest support below price and resistance above price."""
    window = df.tail(lookback)
    price  = df['close'].iloc[-1]
    levels = []

    for i in range(2, len(window) - 2):
        h = window['high'].iloc[i]
        l = window['low'].iloc[i]
        if (h >= window['high'].iloc[i - 1] and h >= window['high'].iloc[i + 1] and
                h >= window['high'].iloc[i - 2] and h >= window['high'].iloc[i + 2]):
            levels.append(h)
        if (l <= window['low'].iloc[i - 1] and l <= window['low'].iloc[i + 1] and
                l <= window['low'].iloc[i - 2] and l <= window['low'].iloc[i + 2]):
            levels.append(l)

    # Cluster levels within tolerance to avoid near-duplicate lines
    clustered = []
    for level in sorted(levels):
        if not clustered or abs(level - clustered[-1]) / clustered[-1] > tolerance:
            clustered.append(level)

    below = [l for l in clustered if l < price]
    above = [l for l in clustered if l > price]
    return {
        'support':    max(below) if below else None,
        'resistance': min(above) if above else None,
        'levels':     clustered,
    }


# ============================================================================
# MARKET STRUCTURE DATA
# ============================================================================

def fetch_funding_rate():
    result = {'rate': 0.0, 'label': 'NEUTRAL', 'bias': 'NEUTRAL', 'rate_pct': 0.0}
    try:
        resp = _session.get('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT', timeout=5)
        resp.raise_for_status()
        rate = float(resp.json().get('lastFundingRate', 0))
        result['rate']     = rate
        result['rate_pct'] = rate * 100
        if rate > 0.05 / 100:
            result['label'] = 'VERY HIGH'
            result['bias']  = 'BEARISH'
        elif rate > 0.01 / 100:
            result['label'] = 'HIGH'
            result['bias']  = 'SLIGHTLY_BEARISH'
        elif rate < -0.01 / 100:
            result['label'] = 'NEGATIVE'
            result['bias']  = 'BULLISH'
    except Exception as e:
        logger.warning(f"Funding Rate fetch failed: {e}")
    return result


def fetch_long_short_ratio():
    result = {'ratio': 1.0, 'long_pct': 50.0, 'short_pct': 50.0, 'bias': 'NEUTRAL'}
    try:
        resp = _session.get(
            'https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
            params={'symbol': 'BTCUSDT', 'period': '1h', 'limit': 1},
            timeout=5,
        )
        resp.raise_for_status()
        data  = resp.json()[0]
        ratio = float(data.get('longShortRatio', 1.0))
        result.update({
            'ratio':     ratio,
            'long_pct':  float(data.get('longAccount',  0.5)) * 100,
            'short_pct': float(data.get('shortAccount', 0.5)) * 100,
        })
        if ratio < 0.8:
            result['bias'] = 'BULLISH'
        elif ratio > 2.0:
            result['bias'] = 'BEARISH'
    except Exception as e:
        logger.warning(f"Long/Short Ratio fetch failed: {e}")
    return result


def fetch_dxy_trend():
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = _session.get(
            'https://query2.finance.yahoo.com/v8/finance/chart/DX-Y.NYB',
            params={'interval': '1d', 'range': '5d'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        closes = resp.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            current, prev = closes[-1], closes[-2]
            change_pct = ((current - prev) / prev) * 100
            result.update({'current': round(current, 3), 'change_pct': round(change_pct, 3)})
            if change_pct > 0.3:
                result['trend'] = 'RISING'
                result['bias']  = 'BEARISH'
            elif change_pct < -0.3:
                result['trend'] = 'FALLING'
                result['bias']  = 'BULLISH'
            else:
                result['trend'] = 'FLAT'
    except Exception as e:
        logger.warning(f"DXY fetch failed: {e}")
    return result


def fetch_sp500_trend():
    """S&P 500 daily trend via Yahoo Finance (free, no key).
    BTC-S&P correlation ~0.60 since 2020 — BTC acts as a leveraged equity beta.
    Rising S&P = risk-on = BULLISH for BTC; falling = risk-off = BEARISH."""
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = _session.get(
            'https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC',
            params={'interval': '1d', 'range': '5d'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        closes = resp.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            current, prev = closes[-1], closes[-2]
            change_pct = ((current - prev) / prev) * 100
            result.update({'current': round(current, 2), 'change_pct': round(change_pct, 3)})
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias']  = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias']  = 'BEARISH'
            else:
                result['trend'] = 'FLAT'
    except Exception as e:
        logger.warning(f"S&P500 fetch failed: {e}")
    return result


def fetch_stablecoin_supply():
    """USDT + USDC combined market cap trend (CoinGecko free API).
    Rising supply = dry powder entering ecosystem = BULLISH for BTC.
    Falling supply = capital leaving = BEARISH.
    Compares to cached value from previous run for trend detection."""
    result = {'total_b': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = _session.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={
                'ids': 'tether,usd-coin',
                'vs_currencies': 'usd',
                'include_market_cap': 'true',
            },
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        resp.raise_for_status()
        data     = resp.json()
        usdt_cap = data.get('tether',   {}).get('usd_market_cap', 0)
        usdc_cap = data.get('usd-coin', {}).get('usd_market_cap', 0)
        total_now = (usdt_cap + usdc_cap) / 1e9   # billions
        result['total_b'] = round(total_now, 1)

        # Compare against cached value from previous cycle
        prev_total = None
        if os.path.exists(STABLECOIN_CACHE_FILE):
            with open(STABLECOIN_CACHE_FILE) as f:
                prev_total = json.load(f).get('total_b')

        with open(STABLECOIN_CACHE_FILE, 'w') as f:
            json.dump({'total_b': total_now, 'ts': datetime.utcnow().isoformat()}, f)

        if prev_total and prev_total > 0:
            change_pct = ((total_now - prev_total) / prev_total) * 100
            result['change_pct'] = round(change_pct, 3)
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias']  = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias']  = 'BEARISH'
    except Exception as e:
        logger.warning(f"Stablecoin supply fetch failed: {e}")
    return result


# ============================================================================
# MULTI-TIMEFRAME ANALYSIS
# ============================================================================

def get_htf_trend():
    htf = {'4h': 'NEUTRAL', '1d': 'NEUTRAL', 'aligned': False}
    try:
        for tf, key in [('4h', '4h'), ('1d', '1d')]:
            bars = exchange.fetch_ohlcv('BTC/USDT', timeframe=tf, limit=220)
            df   = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            ema200 = calculate_ema(df['close'], 200)
            htf[key] = 'BULLISH' if df['close'].iloc[-1] > ema200.iloc[-1] else 'BEARISH'
        htf['aligned'] = htf['4h'] == htf['1d']
    except Exception as e:
        logger.warning(f"HTF fetch failed: {e}")
    return htf


# ============================================================================
# FEAR & GREED INDEX
# ============================================================================

def fetch_fear_and_greed():
    result = {'value': 50, 'label': 'NEUTRAL', 'sentiment': 'NEUTRAL'}
    try:
        resp = _session.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        resp.raise_for_status()
        data = resp.json()['data'][0]
        val  = int(data['value'])
        result['value'] = val
        result['label'] = data['value_classification']
        if val >= 60:
            result['sentiment'] = 'BULLISH'
        elif val <= 40:
            result['sentiment'] = 'BEARISH'
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
    return result


# ============================================================================
# SENTIMENT — CSV headlines + Fear & Greed combined
# ============================================================================

def get_combined_sentiment(fng=None):
    if fng is None:
        fng = fetch_fear_and_greed()

    news_data = {
        'headlines':       [],
        'sentiment':       'NEUTRAL',
        'confidence':      0,
        'fear_greed':      fng,
        'sources_checked': [],
        'geo_bullish':     0,
        'geo_bearish':     0,
    }

    crypto_bullish = crypto_bearish = 0
    geo_bullish    = geo_bearish    = 0

    try:
        df = pd.read_csv(NEWS_CSV)
        # Backward-compatible: old CSVs without category column treated as crypto
        if 'category' not in df.columns:
            df['category'] = 'crypto'

        if not df.empty:
            for _, row in df.head(7).iterrows():
                val = 1 if row['sentiment_label'] == 'BULLISH' else (-1 if row['sentiment_label'] == 'BEARISH' else 0)
                news_data['headlines'].append({
                    'title':    row['title'],
                    'sentiment': val,
                    'source':   row['source'],
                    'category': row.get('category', 'crypto'),
                })

            crypto_df = df[df['category'] == 'crypto']
            geo_df    = df[df['category'] == 'geopolitical']

            crypto_bullish = len(crypto_df[crypto_df['sentiment_label'] == 'BULLISH'])
            crypto_bearish = len(crypto_df[crypto_df['sentiment_label'] == 'BEARISH'])
            geo_bullish    = len(geo_df[geo_df['sentiment_label'] == 'BULLISH'])
            geo_bearish    = len(geo_df[geo_df['sentiment_label'] == 'BEARISH'])

            news_data['sources_checked'] = df['source'].unique().tolist()
            news_data['geo_bullish']     = geo_bullish
            news_data['geo_bearish']     = geo_bearish
    except Exception:
        pass  # stale or missing CSV — fall back to F&G only

    # Three-way blend: F&G 50% + crypto news 35% + geopolitical 15%
    fng_score    = (fng['value'] - 50) / 50
    crypto_score = (crypto_bullish - crypto_bearish) / max(crypto_bullish + crypto_bearish, 1)
    geo_score    = (geo_bullish    - geo_bearish)    / max(geo_bullish    + geo_bearish,    1)
    combined     = (fng_score * 0.50) + (crypto_score * 0.35) + (geo_score * 0.15)

    if combined > 0.15:
        news_data['sentiment']  = 'BULLISH'
        news_data['confidence'] = min(combined * 100, 100)
    elif combined < -0.15:
        news_data['sentiment']  = 'BEARISH'
        news_data['confidence'] = min(abs(combined) * 100, 100)

    return news_data


# ============================================================================
# MACRO EVENT CHECK
# ============================================================================

def check_upcoming_macro_events():
    """Returns (bool, event_name). True only when a HIGH impact USD event is ≤2h away."""
    try:
        df      = pd.read_csv(MACRO_CSV)
        pending = df[(df['actual'].isna()) | (df['actual'] == 'N/A')]
        pending = pending[pending['impact'] == 'High']
        for _, row in pending.iterrows():
            try:
                event_dt = datetime.strptime(row['timestamp'].strip(), "%m-%d-%Y %I:%M%p")
                diff     = event_dt - datetime.utcnow()
                if timedelta(0) <= diff <= timedelta(hours=2):
                    return True, row['event']
            except Exception:
                continue
    except Exception:
        pass
    return False, None


# ============================================================================
# SIGNAL GENERATION
# ============================================================================

def generate_signals(df, htf=None, market_structure=None, sr=None):
    current  = df.iloc[-1]
    previous = df.iloc[-2]

    atr_stop = current['ATR_14'] * RISK_CONFIG['atr_multiplier']

    signal = {
        'type':               'HOLD',
        'strength':           0,
        'reasons':            [],
        'entry_price':        current['close'],
        'stop_loss':          None,
        'take_profit':        None,
        'atr':                current['ATR_14'],
        'support_resistance': sr or {},
    }

    buy_conditions  = 0.0
    sell_conditions = 0.0

    # 1 — EMA 200 trend
    if current['close'] > current['EMA_200']:
        buy_conditions += 1
        signal['reasons'].append("✓ Price above EMA 200 (bullish trend)")
    else:
        sell_conditions += 1
        signal['reasons'].append("✗ Price below EMA 200 (bearish trend)")

    # 2 — RSI
    rsi = current['RSI_14']
    if 30 < rsi < 50:
        buy_conditions += 1
        signal['reasons'].append("✓ RSI in buy zone (30–50)")
    elif rsi <= 30:
        buy_conditions += 1.5
        signal['reasons'].append("✓ RSI OVERSOLD (<30) — strong buy signal")
    elif rsi > 70:
        sell_conditions += 1.5
        signal['reasons'].append("✗ RSI OVERBOUGHT (>70) — strong sell signal")
    elif rsi > 55:
        sell_conditions += 0.5
        signal['reasons'].append("✗ RSI elevated (>55)")

    # 3 — MACD crossover / position
    if current['MACD'] > current['MACD_Signal'] and previous['MACD'] <= previous['MACD_Signal']:
        buy_conditions += 1.5
        signal['reasons'].append("✓ MACD bullish crossover")
    elif current['MACD'] > current['MACD_Signal']:
        buy_conditions += 0.5
        signal['reasons'].append("✓ MACD above signal line")
    elif current['MACD'] < current['MACD_Signal'] and previous['MACD'] >= previous['MACD_Signal']:
        sell_conditions += 1.5
        signal['reasons'].append("✗ MACD bearish crossover")
    elif current['MACD'] < current['MACD_Signal']:
        sell_conditions += 0.5
        signal['reasons'].append("✗ MACD below signal line")

    # 4 — Volume confirmation
    vol_avg   = df['volume'].rolling(20).mean().iloc[-1]
    vol_ratio = current['volume'] / vol_avg if vol_avg > 0 else 1
    if vol_ratio >= 1.3:
        if current['close'] > previous['close']:
            buy_conditions += 1
            signal['reasons'].append(f"✓ Volume confirms UP move ({vol_ratio:.1f}x avg)")
        else:
            sell_conditions += 1
            signal['reasons'].append(f"✗ Volume confirms DOWN move ({vol_ratio:.1f}x avg)")
    elif vol_ratio < 0.7:
        signal['reasons'].append(f"⚠️  Low volume ({vol_ratio:.1f}x avg) — weak conviction")

    # 5 — Bollinger Bands
    if current['close'] <= current['BB_Lower']:
        buy_conditions += 1
        signal['reasons'].append("✓ Price at/below BB Lower — oversold")
    elif current['close'] >= current['BB_Upper']:
        sell_conditions += 1
        signal['reasons'].append("✗ Price at/above BB Upper — overbought")
    elif current['close'] > current['BB_Middle']:
        buy_conditions += 0.25
    else:
        sell_conditions += 0.25

    # 6 — Multi-timeframe alignment
    if htf:
        if htf['aligned']:
            if htf['4h'] == 'BULLISH':
                buy_conditions += 1.5
                signal['reasons'].append(f"✓ HTF Aligned BULLISH (4H: {htf['4h']}, 1D: {htf['1d']})")
            elif htf['4h'] == 'BEARISH':
                sell_conditions += 1.5
                signal['reasons'].append(f"✗ HTF Aligned BEARISH (4H: {htf['4h']}, 1D: {htf['1d']})")
        else:
            signal['reasons'].append(f"⚠️  HTF Disagreement (4H: {htf['4h']}, 1D: {htf['1d']}) — caution")

    # 7 — RSI Divergence
    divergence = detect_rsi_divergence(df)
    signal['rsi_divergence'] = divergence
    if divergence == 'BULLISH':
        buy_conditions += 2.0
        signal['reasons'].append("✓ RSI BULLISH DIVERGENCE — price lower low, RSI higher low")
    elif divergence == 'BEARISH':
        sell_conditions += 2.0
        signal['reasons'].append("✗ RSI BEARISH DIVERGENCE — price higher high, RSI lower high")

    # 8 — OBV slope (5-candle)
    obv_slope = df['OBV'].iloc[-1] - df['OBV'].iloc[-5]
    if obv_slope > 0:
        buy_conditions += 0.75
        signal['reasons'].append("✓ OBV rising — accumulation detected")
    else:
        sell_conditions += 0.75
        signal['reasons'].append("✗ OBV falling — distribution detected")

    # 9 — Market structure (funding rate, L/S ratio, DXY)
    if market_structure:
        funding = market_structure.get('funding', {})
        ls      = market_structure.get('long_short', {})
        dxy     = market_structure.get('dxy', {})

        if funding.get('bias') == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ Funding negative ({funding.get('rate_pct', 0):.4f}%) — shorts dominant")
        elif funding.get('bias') == 'BEARISH':
            sell_conditions += 1.0
            signal['reasons'].append(f"✗ Funding VERY HIGH ({funding.get('rate_pct', 0):.4f}%) — longs overleveraged")
        elif funding.get('bias') == 'SLIGHTLY_BEARISH':
            sell_conditions += 0.25
            signal['reasons'].append(f"✗ Funding elevated ({funding.get('rate_pct', 0):.4f}%)")

        if ls.get('bias') == 'BULLISH':
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ L/S Ratio {ls.get('ratio', 1):.2f} — shorts crowded, squeeze risk")
        elif ls.get('bias') == 'BEARISH':
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ L/S Ratio {ls.get('ratio', 1):.2f} — longs crowded")

        if dxy.get('bias') == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ DXY FALLING ({dxy.get('change_pct', 0):+.2f}%) — weak USD")
        elif dxy.get('bias') == 'BEARISH':
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ DXY RISING ({dxy.get('change_pct', 0):+.2f}%) — strong USD")

    # 10 — S&P 500 trend (Tier-1 macro: BTC correlation ~0.60 since 2020)
    if market_structure:
        sp500 = market_structure.get('sp500', {})
        if sp500.get('bias') == 'BULLISH':
            buy_conditions += 1.0
            signal['reasons'].append(f"✓ S&P500 RISING ({sp500.get('change_pct',0):+.2f}%) — risk-on, BTC follows")
        elif sp500.get('bias') == 'BEARISH':
            sell_conditions += 1.0
            signal['reasons'].append(f"✗ S&P500 FALLING ({sp500.get('change_pct',0):+.2f}%) — risk-off, BTC follows")

        # 11 — Stablecoin supply (dry powder indicator)
        stable = market_structure.get('stablecoin', {})
        if stable.get('bias') == 'BULLISH':
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Stablecoin supply RISING (${stable.get('total_b',0):.0f}B) — dry powder entering")
        elif stable.get('bias') == 'BEARISH':
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Stablecoin supply FALLING (${stable.get('total_b',0):.0f}B) — capital leaving")

    # 13 — Stochastic RSI
    sk, sd  = current.get('StochRSI_K'), current.get('StochRSI_D')
    psk, psd = previous.get('StochRSI_K'), previous.get('StochRSI_D')
    if not any(pd.isna(v) for v in [sk, sd, psk, psd] if v is not None):
        if sk < 20 and sk > sd and psk <= psd:
            buy_conditions += 1.0
            signal['reasons'].append(f"✓ StochRSI oversold crossover K={sk:.1f} — bullish momentum")
        elif sk > 80 and sk < sd and psk >= psd:
            sell_conditions += 1.0
            signal['reasons'].append(f"✗ StochRSI overbought crossover K={sk:.1f} — bearish momentum")
        elif sk < 20:
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ StochRSI oversold (K={sk:.1f})")
        elif sk > 80:
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ StochRSI overbought (K={sk:.1f})")

    # 14 — Support/Resistance proximity (within 0.3%)
    if sr:
        support    = sr.get('support')
        resistance = sr.get('resistance')
        price      = current['close']
        prox       = 0.003
        if support and abs(price - support) / price <= prox and current['close'] >= previous['close']:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Bouncing off support ${support:,.0f}")
        elif resistance and abs(price - resistance) / price <= prox and current['close'] <= previous['close']:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Rejected at resistance ${resistance:,.0f}")

    # 12 — VWAP position
    vwap = current.get('VWAP_24')
    if vwap and not pd.isna(vwap):
        if current['close'] > vwap:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Price above VWAP ${vwap:,.2f} — institutional buying")
        else:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Price below VWAP ${vwap:,.2f} — institutional selling")

    # Determine final signal
    if buy_conditions >= SIGNAL_THRESHOLD and buy_conditions > sell_conditions:
        signal['type']        = 'BUY'
        signal['strength']    = buy_conditions
        signal['stop_loss']   = current['close'] - atr_stop
        signal['take_profit'] = current['close'] + (atr_stop * RISK_CONFIG['take_profit_rr'])
    elif sell_conditions >= SIGNAL_THRESHOLD and sell_conditions > buy_conditions:
        signal['type']        = 'SELL'
        signal['strength']    = sell_conditions
        signal['stop_loss']   = current['close'] + atr_stop
        signal['take_profit'] = current['close'] - (atr_stop * RISK_CONFIG['take_profit_rr'])
    else:
        signal['type']     = 'HOLD'
        signal['strength'] = max(buy_conditions, sell_conditions)

    return signal


# ============================================================================
# NEWS + MACRO INTEGRATION
# ============================================================================

def integrate_news_with_signal(signal, news_data):
    enhanced = signal.copy()

    has_macro, event_name = check_upcoming_macro_events()
    if has_macro:
        enhanced['type']     = 'HOLD'
        enhanced['strength'] = 0
        enhanced['reasons'].append(f"⚠️  FORCED HOLD: HIGH impact event in <2h ({event_name})")

    fng     = news_data.get('fear_greed', {})
    fng_val = fng.get('value', 50)

    if fng_val <= 20 and enhanced['type'] == 'BUY':
        enhanced['strength'] += 1.5
        enhanced['reasons'].append(f"🔴 EXTREME FEAR ({fng_val}) — contrarian BUY confirmed")
    elif fng_val >= 80 and enhanced['type'] == 'SELL':
        enhanced['strength'] += 1.5
        enhanced['reasons'].append(f"🟢 EXTREME GREED ({fng_val}) — contrarian SELL confirmed")
    elif news_data.get('sentiment') == 'BULLISH' and enhanced['type'] == 'BUY':
        enhanced['strength'] += 0.75
        enhanced['reasons'].append(f"📰 Sentiment BULLISH (F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BEARISH' and enhanced['type'] == 'SELL':
        enhanced['strength'] += 0.75
        enhanced['reasons'].append(f"📰 Sentiment BEARISH (F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BEARISH' and enhanced['type'] == 'BUY':
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  Sentiment contradicts (BEARISH, F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BULLISH' and enhanced['type'] == 'SELL':
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  Sentiment contradicts (BULLISH, F&G: {fng_val})")

    enhanced['news_sentiment']  = news_data.get('sentiment', 'NEUTRAL')
    enhanced['news_confidence'] = news_data.get('confidence', 0)
    enhanced['fear_greed_value'] = fng_val
    enhanced['fear_greed_label'] = fng.get('label', 'Neutral')
    return enhanced


# ============================================================================
# POSITION SIZING
# ============================================================================

def calculate_position_size(signal, account_balance=None):
    if account_balance is None:
        account_balance = RISK_CONFIG['account_balance']
    if signal['type'] == 'HOLD' or not signal.get('stop_loss'):
        return {'usdt_amount': 0, 'btc_amount': 0, 'position_ratio': 0, 'risk_amount': 0}

    risk_amount   = account_balance * RISK_CONFIG['risk_per_trade']
    max_position  = account_balance * RISK_CONFIG['max_position_size']
    price_diff    = abs(signal['entry_price'] - signal['stop_loss'])
    position_size = (risk_amount / price_diff) * signal['entry_price'] if price_diff > 0 else max_position
    position_size = min(position_size, max_position)

    return {
        'usdt_amount':    position_size,
        'btc_amount':     position_size / signal['entry_price'],
        'position_ratio': (position_size / account_balance) * 100,
        'risk_amount':    risk_amount,
    }


def calculate_futures_position(signal):
    if signal['type'] == 'HOLD' or not signal.get('stop_loss'):
        return None

    balance          = FUTURES_CONFIG['futures_balance']
    entry, sl, tp    = signal['entry_price'], signal['stop_loss'], signal['take_profit']
    sl_distance_pct  = abs(entry - sl) / entry

    direction        = 'LONG' if signal['type'] == 'BUY' else 'SHORT'
    risk_pct         = FUTURES_CONFIG['risk_per_trade']
    optimal_leverage = risk_pct / sl_distance_pct if sl_distance_pct > 0 else 1
    leverage         = max(1, min(int(optimal_leverage), FUTURES_CONFIG['max_leverage']))

    max_margin     = balance * FUTURES_CONFIG['max_margin_pct']
    risk_amount    = balance * risk_pct
    margin         = risk_amount / (sl_distance_pct * leverage) if sl_distance_pct > 0 else max_margin
    margin         = min(margin, max_margin)
    position_value = margin * leverage
    btc_amount     = position_value / entry

    if direction == 'LONG':
        pnl_at_tp    = (tp - entry) / entry * position_value
        pnl_at_sl    = (sl - entry) / entry * position_value
        liquidation  = entry * (1 - (1 / leverage) * 0.95)
    else:
        pnl_at_tp    = (entry - tp) / entry * position_value
        pnl_at_sl    = (entry - sl) / entry * position_value
        liquidation  = entry * (1 + (1 / leverage) * 0.95)

    tier = 'CONSERVATIVE' if leverage <= 3 else ('MODERATE' if leverage <= 7 else 'AGGRESSIVE')

    return {
        'direction':        direction,
        'leverage':         leverage,
        'tier':             tier,
        'margin':           margin,
        'position_value':   position_value,
        'btc_amount':       btc_amount,
        'entry':            entry,
        'stop_loss':        sl,
        'take_profit':      tp,
        'liquidation_price': liquidation,
        'pnl_at_tp':        pnl_at_tp,
        'pnl_at_sl':        pnl_at_sl,
        'risk_amount':      risk_amount,
        'margin_pct':       (margin / balance) * 100,
    }


# ============================================================================
# DATA FETCHING
# ============================================================================

def fetch_ohlcv_df(symbol='BTC/USDT', timeframe='1h', limit=500):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df   = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    df['EMA_200']                         = calculate_ema(df['close'], 200)
    df['RSI_14']                          = calculate_rsi(df['close'])
    df['MACD'], df['MACD_Signal'], df['MACD_Histogram'] = calculate_macd(df['close'])
    df['BB_Upper'], df['BB_Middle'], df['BB_Lower']     = calculate_bollinger_bands(df['close'])
    df['ATR_14']                          = calculate_atr(df)
    df['OBV']                             = calculate_obv(df)
    df['StochRSI_K'], df['StochRSI_D']   = calculate_stoch_rsi(df['close'])
    df['VWAP_24']                         = calculate_vwap(df)

    return df


# ============================================================================
# SIGNAL HISTORY
# ============================================================================

def log_signal(signal, df, htf=None):
    """Append one row to signal_history.csv for performance tracking."""
    last = df.iloc[-1]
    row = {
        'timestamp':      datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'type':           signal['type'],
        'entry_price':    signal['entry_price'],
        'stop_loss':      signal.get('stop_loss', ''),
        'take_profit':    signal.get('take_profit', ''),
        'strength':       round(signal['strength'], 2),
        'rsi':            round(last['RSI_14'], 2),
        'stoch_k':        round(last.get('StochRSI_K') or 0, 2),
        'vwap':           round(last.get('VWAP_24') or 0, 2),
        'htf_4h':         htf.get('4h', '') if htf else '',
        'htf_1d':         htf.get('1d', '') if htf else '',
        'fear_greed':     signal.get('fear_greed_value', ''),
        'news_sentiment': signal.get('news_sentiment', ''),
        'outcome':        '',   # fill manually: WIN / LOSS / BREAKEVEN
    }
    import os
    write_header = not os.path.exists(SIGNAL_HISTORY_CSV)
    pd.DataFrame([row]).to_csv(SIGNAL_HISTORY_CSV, mode='a', header=write_header, index=False)
    logger.info(f"Signal logged → {SIGNAL_HISTORY_CSV}")


# ============================================================================
# MAIN ANALYSIS PIPELINE
# ============================================================================

def analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True):
    """
    Full pipeline:
    1. Fetch 1H OHLCV + compute all indicators
    2. Detect support/resistance levels
    3. Fetch HTF trend, market structure, and (if include_news) F&G in parallel
    4. Generate weighted signal score
    5. Integrate news/macro overlay
    6. Display structured report
    Returns the final signal dict, or None on fatal error.
    """
    logger.info(f"Analyzing {symbol} ({timeframe})...")

    try:
        df = fetch_ohlcv_df(symbol, timeframe)
        sr = detect_support_resistance(df)

        logger.info("Fetching market data in parallel...")
        futures_map = {}
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures_map['htf']         = pool.submit(get_htf_trend)
            futures_map['funding']     = pool.submit(fetch_funding_rate)
            futures_map['ls']          = pool.submit(fetch_long_short_ratio)
            futures_map['dxy']         = pool.submit(fetch_dxy_trend)
            futures_map['sp500']       = pool.submit(fetch_sp500_trend)
            futures_map['stablecoin']  = pool.submit(fetch_stablecoin_supply)
            if include_news:
                futures_map['fng']     = pool.submit(fetch_fear_and_greed)

            htf = futures_map['htf'].result()
            market_structure = {
                'funding':     futures_map['funding'].result(),
                'long_short':  futures_map['ls'].result(),
                'dxy':         futures_map['dxy'].result(),
                'sp500':       futures_map['sp500'].result(),
                'stablecoin':  futures_map['stablecoin'].result(),
            }
            fng = futures_map['fng'].result() if include_news else None

        signal = generate_signals(df, htf, market_structure, sr)

        news_data = None
        if include_news:
            logger.info("Computing combined sentiment...")
            news_data = get_combined_sentiment(fng=fng)
            signal    = integrate_news_with_signal(signal, news_data)

        display_analysis(df, signal, news_data, htf, market_structure)
        log_signal(signal, df, htf)
        return signal

    except Exception as e:
        logger.error(f"{type(e).__name__}: {str(e)[:120]}")
        return None


# ============================================================================
# DISPLAY
# ============================================================================

def display_analysis(df, signal, news_data, htf=None, market_structure=None):
    last = df.iloc[-1]
    sr   = signal.get('support_resistance', {})

    print("=" * 70)
    print("  🚀 BTC/USDT TRADING ANALYSIS — PRODUCTION EDITION".center(70))
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC".center(70))
    print("=" * 70)

    print("\n📊 MARKET DATA:")
    trend = "BULLISH ⬆️" if last['close'] > last['EMA_200'] else "BEARISH ⬇️"
    print(f"   Price:            ${last['close']:,.2f}")
    print(f"   EMA 200:          ${last['EMA_200']:,.2f}  [{trend}]")
    print(f"   24h High:         ${df['high'].tail(24).max():,.2f}")
    print(f"   24h Low:          ${df['low'].tail(24).min():,.2f}")
    if sr.get('resistance'):
        print(f"   Resistance:       ${sr['resistance']:,.2f}")
    if sr.get('support'):
        print(f"   Support:          ${sr['support']:,.2f}")

    if htf:
        aligned_str = "✓ ALIGNED" if htf['aligned'] else "✗ DIVERGING"
        print(f"\n🗺️  MULTI-TIMEFRAME:")
        print(f"   4H Trend:         {htf['4h']}")
        print(f"   1D Trend:         {htf['1d']}")
        print(f"   Alignment:        {aligned_str}")

    print(f"\n📈 TECHNICAL INDICATORS:")
    rsi_tag = " ⚠️ OVERBOUGHT" if last['RSI_14'] > 70 else (" ✓ OVERSOLD" if last['RSI_14'] < 30 else "")
    print(f"   RSI (14):         {last['RSI_14']:.2f}{rsi_tag}")
    macd_tag = "✓ Bullish" if last['MACD'] > last['MACD_Signal'] else "✗ Bearish"
    print(f"   MACD:             {last['MACD']:.2f}  [{macd_tag}]")
    sk, sd = last.get('StochRSI_K'), last.get('StochRSI_D')
    if sk is not None and sd is not None and not (pd.isna(sk) or pd.isna(sd)):
        zone = " ⚠️ OVERBOUGHT" if sk > 80 else (" ✓ OVERSOLD" if sk < 20 else "")
        print(f"   StochRSI K/D:     {sk:.1f} / {sd:.1f}{zone}")
    vwap = last.get('VWAP_24')
    if vwap and not pd.isna(vwap):
        vwap_tag = "✓ Bullish" if last['close'] > vwap else "✗ Bearish"
        print(f"   VWAP (24h):       ${vwap:,.2f}  [{vwap_tag}]")
    print(f"   BB Upper:         ${last['BB_Upper']:,.2f}")
    print(f"   BB Middle:        ${last['BB_Middle']:,.2f}")
    print(f"   BB Lower:         ${last['BB_Lower']:,.2f}")
    print(f"   ATR (14):         ${last['ATR_14']:.2f}")

    if market_structure:
        funding = market_structure.get('funding', {})
        ls      = market_structure.get('long_short', {})
        dxy     = market_structure.get('dxy', {})
        div     = signal.get('rsi_divergence', 'NONE')
        div_icon = '📈' if div == 'BULLISH' else ('📉' if div == 'BEARISH' else '➡️')
        print(f"\n🏗️  MARKET STRUCTURE:")
        print(f"   Funding Rate:     {funding.get('rate_pct', 0):.5f}%  [{funding.get('label','N/A')} — {funding.get('bias','N/A')}]")
        print(f"   Long/Short Ratio: {ls.get('ratio', 1):.3f}  "
              f"(L:{ls.get('long_pct',50):.1f}% / S:{ls.get('short_pct',50):.1f}%)  [{ls.get('bias','N/A')}]")
        print(f"   DXY:              {dxy.get('current', 0):.3f}  ({dxy.get('change_pct', 0):+.3f}%)  "
              f"[{dxy.get('trend','N/A')} — {dxy.get('bias','N/A')}]")
        obv_slope = df['OBV'].iloc[-1] - df['OBV'].iloc[-5]
        print(f"   OBV (5c slope):   {obv_slope:+,.0f}  [{'📈 Accumulation' if obv_slope > 0 else '📉 Distribution'}]")
        print(f"   RSI Divergence:   {div_icon} {div}")
        sp500 = market_structure.get('sp500', {})
        if sp500.get('current'):
            sp_icon = '🟢' if sp500['bias'] == 'BULLISH' else ('🔴' if sp500['bias'] == 'BEARISH' else '⚪')
            print(f"   S&P 500:          {sp_icon} {sp500['current']:,.0f}  ({sp500['change_pct']:+.2f}%)  [{sp500['trend']}]")
        stable = market_structure.get('stablecoin', {})
        if stable.get('total_b'):
            st_icon = '🟢' if stable['bias'] == 'BULLISH' else ('🔴' if stable['bias'] == 'BEARISH' else '⚪')
            print(f"   Stablecoin Supply:{st_icon} ${stable['total_b']:.0f}B  ({stable['change_pct']:+.3f}%)  [{stable['trend']}]")

    if news_data and news_data.get('fear_greed'):
        fng = news_data['fear_greed']
        val = fng['value']
        bar = "█" * (val // 10) + "░" * (10 - val // 10)
        print(f"\n😱 FEAR & GREED INDEX:")
        print(f"   [{bar}] {val}/100 — {fng['label']}")
        print(f"   Market Bias:      {fng['sentiment']}")

    if news_data:
        geo_b = news_data.get('geo_bullish', 0)
        geo_r = news_data.get('geo_bearish', 0)
        print(f"\n📰 COMBINED SENTIMENT:")
        print(f"   Overall:          {signal.get('news_sentiment', 'NEUTRAL')}  "
              f"(Confidence: {signal.get('news_confidence', 0):.0f}%)")
        if geo_b + geo_r > 0:
            geo_label = 'BULLISH' if geo_b > geo_r else ('BEARISH' if geo_r > geo_b else 'NEUTRAL')
            print(f"   Geopolitical:     {geo_label}  ({geo_b} risk events → BTC safe-haven, {geo_r} stability events)")
        for i, h in enumerate(news_data.get('headlines', [])[:4], 1):
            icon = "📈" if h['sentiment'] > 0 else ("📉" if h['sentiment'] < 0 else "➡️")
            cat  = "🌍" if h.get('category') == 'geopolitical' else "📰"
            print(f"   {i}. {cat}{icon} {h['title'][:62]}...")

    s_icons = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}
    print(f"\n🎯 TRADING SIGNAL:")
    print(f"   Action:           {s_icons.get(signal['type'],'')} {signal['type']}")
    print(f"   Signal Strength:  {signal['strength']:.2f} / {SIGNAL_MAX_SCORE}")

    if signal['type'] != 'HOLD' and signal.get('stop_loss'):
        rr     = abs(signal['take_profit'] - signal['entry_price']) / abs(signal['entry_price'] - signal['stop_loss'])
        sl_pct = abs(signal['entry_price'] - signal['stop_loss']) / signal['entry_price'] * 100
        tp_pct = abs(signal['take_profit'] - signal['entry_price']) / signal['entry_price'] * 100
        print(f"   Entry:            ${signal['entry_price']:,.2f}")
        print(f"   Stop Loss:        ${signal['stop_loss']:,.2f}  (-{sl_pct:.2f}% | ATR-based)")
        print(f"   Take Profit:      ${signal['take_profit']:,.2f}  (+{tp_pct:.2f}%)")
        print(f"   Risk/Reward:      1 : {rr:.2f}")

    pos = calculate_position_size(signal)
    print(f"\n💰 SPOT POSITION SIZING:")
    print(f"   Account Balance:  ${RISK_CONFIG['account_balance']:,.2f} USDT")
    print(f"   Max Risk/Trade:   ${pos['risk_amount']:.2f} USDT  ({RISK_CONFIG['risk_per_trade']*100:.0f}%)")
    if signal['type'] != 'HOLD':
        print(f"   Position Size:    ${pos['usdt_amount']:.2f} USDT")
        print(f"   BTC Amount:       {pos['btc_amount']:.6f} BTC")
        print(f"   Portfolio %:      {pos['position_ratio']:.2f}%")
    else:
        print(f"   Position Size:    NONE (Hold — no trade)")

    futures = calculate_futures_position(signal)
    if futures:
        f_icon    = '🟢 LONG ⬆️' if futures['direction'] == 'LONG' else '🔴 SHORT ⬇️'
        tier_icon = '🛡️' if futures['tier'] == 'CONSERVATIVE' else ('⚖️' if futures['tier'] == 'MODERATE' else '🔥')
        print(f"\n📊 FUTURES SIGNAL:")
        print(f"   Direction:        {f_icon}")
        print(f"   Leverage:         {futures['leverage']}x  [{tier_icon} {futures['tier']}]")
        print(f"   ─────────────────────────────────────────────")
        print(f"   Entry:            ${futures['entry']:,.2f}")
        print(f"   Stop Loss:        ${futures['stop_loss']:,.2f}")
        print(f"   Take Profit:      ${futures['take_profit']:,.2f}")
        print(f"   Liquidation:      ${futures['liquidation_price']:,.2f}  ⚠️")
        print(f"   ─────────────────────────────────────────────")
        print(f"   Margin Required:  ${futures['margin']:.2f} USDT  ({futures['margin_pct']:.1f}% of balance)")
        print(f"   Position Value:   ${futures['position_value']:,.2f} USDT")
        print(f"   BTC Amount:       {futures['btc_amount']:.6f} BTC")
        print(f"   ─────────────────────────────────────────────")
        print(f"   If TP hit:        +${futures['pnl_at_tp']:,.2f} USDT  ✅")
        print(f"   If SL hit:        ${futures['pnl_at_sl']:,.2f} USDT  ❌")
        print(f"   Max Risk:         ${futures['risk_amount']:.2f} USDT  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}% of futures balance)")
    elif signal['type'] == 'HOLD':
        print(f"\n📊 FUTURES SIGNAL:")
        print(f"   Direction:        🟡 NO POSITION — Hold")

    print(f"\n📋 SIGNAL REASONS ({len(signal['reasons'])} factors):")
    for r in signal['reasons']:
        print(f"   {r}")

    print("=" * 70 + "\n")


# ============================================================================
# STANDALONE ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                        stream=sys.stdout)
    analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True)
