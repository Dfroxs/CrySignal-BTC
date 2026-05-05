#!/usr/bin/env python3
"""BTC/USDT Trading Signal Generator.

16 weighted technical + macro conditions summed into BUY / SELL / HOLD
signals with ATR-based SL/TP, multi-timeframe alignment, and news overlay.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import ccxt
import numpy as np
import pandas as pd

from config import (
    BTC_DOM_CACHE_FILE,
    FUTURES_CONFIG,
    HTTP_SESSION,
    MACRO_CSV,
    NEWS_CSV,
    OI_CACHE_FILE,
    RISK_CONFIG,
    SIGNAL_MAX_SCORE,
    SIGNAL_THRESHOLD,
    STABLECOIN_CACHE_FILE,
    THRESHOLD_STATE_FILE,
    THRESHOLD_MIN,
    THRESHOLD_MAX,
    ADAPTIVE_WINDOW_HOURS,
    ADAPTIVE_MAX_SIGNALS,
    load_cache,
    save_cache,
)
from signal_history import log_signal

logger = logging.getLogger(__name__)

exchange = ccxt.binance()

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
    """Fetch funding rate + futures basis (mark vs index price spread).

    Returns a dict with both ``funding`` and ``basis`` sub-dicts which
    feed separate scoring conditions.
    """
    result = {
        'rate': 0.0, 'label': 'NEUTRAL', 'bias': 'NEUTRAL', 'rate_pct': 0.0,
        'basis_pct': 0.0, 'basis_bias': 'NEUTRAL',
    }
    try:
        resp = HTTP_SESSION.get(
            'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT',
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data.get('lastFundingRate', 0))
        result['rate']     = rate
        result['rate_pct'] = rate * 100

        # Funding bias
        if rate > 0.05 / 100:
            result['label'] = 'VERY HIGH'
            result['bias']  = 'BEARISH'
        elif rate > 0.01 / 100:
            result['label'] = 'HIGH'
            result['bias']  = 'SLIGHTLY_BEARISH'
        elif rate < -0.01 / 100:
            result['label'] = 'NEGATIVE'
            result['bias']  = 'BULLISH'

        # Futures basis: (mark - index) / index
        mark  = float(data.get('markPrice', 0))
        index = float(data.get('indexPrice', 1))
        if index > 0:
            basis = ((mark - index) / index) * 100
            result['basis_pct'] = round(basis, 4)
            if basis > 0.10:
                result['basis_bias'] = 'BULLISH'
            elif basis < -0.10:
                result['basis_bias'] = 'BEARISH'

    except Exception as e:
        logger.warning("Funding Rate fetch failed: %s", e)
    return result


def fetch_long_short_ratio():
    result = {'ratio': 1.0, 'long_pct': 50.0, 'short_pct': 50.0, 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get(
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
        resp = HTTP_SESSION.get(
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
        resp = HTTP_SESSION.get(
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
        resp = HTTP_SESSION.get(
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
            json.dump({'total_b': total_now, 'ts': datetime.now(UTC).isoformat()}, f)

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


def fetch_btc_dominance():
    """BTC dominance via CoinGecko /global (free, no key).
    Rising BTC.D = capital rotating into BTC (bullish).
    Falling BTC.D = alt-season, capital rotating out (bearish for BTC)."""
    result = {'current': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get('https://api.coingecko.com/api/v3/global',
                            headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        resp.raise_for_status()
        data     = resp.json()['data']
        btc_dom  = data.get('market_cap_percentage', {}).get('btc', 0)
        result['current'] = round(btc_dom, 1)

        prev_btc_dom = None
        if os.path.exists(BTC_DOM_CACHE_FILE):
            with open(BTC_DOM_CACHE_FILE) as f:
                prev_btc_dom = json.load(f).get('btc_dom')

        with open(BTC_DOM_CACHE_FILE, 'w') as f:
            json.dump({'btc_dom': btc_dom, 'ts': datetime.now(UTC).isoformat()}, f)

        if prev_btc_dom and prev_btc_dom > 0:
            change_pct = btc_dom - prev_btc_dom
            result['change_pct'] = round(change_pct, 2)
            if change_pct > 0.5:
                result['trend'] = 'RISING'
                result['bias']  = 'BULLISH'
            elif change_pct < -0.5:
                result['trend'] = 'FALLING'
                result['bias']  = 'BEARISH'
    except Exception as e:
        logger.warning(f"BTC dominance fetch failed: {e}")
    return result


def fetch_open_interest():
    """Open Interest via Binance Futures /openInterest (free, no key).
    Rising OI confirms trend; contradicting OI warns of reversal.
    Compares to cached value from previous run for trend detection."""
    result = {'notional': 0.0, 'change_pct': 0.0, 'trend': 'NEUTRAL', 'bias': 'NEUTRAL'}
    try:
        resp = HTTP_SESSION.get('https://fapi.binance.com/fapi/v1/openInterest',
                            params={'symbol': 'BTCUSDT'}, timeout=5)
        resp.raise_for_status()
        oi = float(resp.json().get('openInterest', 0))
        result['notional'] = round(oi, 2)

        prev_oi = None
        if os.path.exists(OI_CACHE_FILE):
            with open(OI_CACHE_FILE) as f:
                prev_oi = json.load(f).get('oi')

        with open(OI_CACHE_FILE, 'w') as f:
            json.dump({'oi': oi, 'ts': datetime.now(UTC).isoformat()}, f)

        if prev_oi and prev_oi > 0:
            change_pct = ((oi - prev_oi) / prev_oi) * 100
            result['change_pct'] = round(change_pct, 3)
            if change_pct > 1.0:
                result['trend'] = 'RISING'
                result['bias']  = 'BULLISH'
            elif change_pct < -1.0:
                result['trend'] = 'FALLING'
                result['bias']  = 'BEARISH'
    except Exception as e:
        logger.warning("Open Interest fetch failed: %s", e)
    return result


def get_adaptive_threshold():
    """Return the current adaptive SIGNAL_THRESHOLD.

    Reads signal count from the rolling window in *THRESHOLD_STATE_FILE*.
    Raises threshold if too many signals, lowers if none.
    """
    threshold = float(os.getenv("SIGNAL_THRESHOLD", 0))
    if threshold > 0:
        return threshold

    state = load_cache(THRESHOLD_STATE_FILE)
    base = SIGNAL_THRESHOLD
    now = datetime.now(UTC)

    signals = state.get("signals", [])
    cutoff = (now - timedelta(hours=ADAPTIVE_WINDOW_HOURS)).isoformat()
    recent = [ts for ts in signals if ts > cutoff]

    if len(recent) > ADAPTIVE_MAX_SIGNALS:
        base = min(base + 0.5, THRESHOLD_MAX)
    elif len(recent) == 0 and len(signals) > 0:
        base = max(base - 0.25, THRESHOLD_MIN)

    return base


def update_threshold_state(signal_type):
    """Record that a signal was fired for adaptive threshold tracking."""
    if signal_type == "HOLD":
        return
    state = load_cache(THRESHOLD_STATE_FILE)
    signals = state.get("signals", [])
    signals.append(datetime.now(UTC).isoformat())
    now = datetime.now(UTC)
    cutoff = (now - timedelta(hours=ADAPTIVE_WINDOW_HOURS * 2)).isoformat()
    signals = [ts for ts in signals if ts > cutoff]
    save_cache(THRESHOLD_STATE_FILE, {"signals": signals})


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
        resp = HTTP_SESSION.get('https://api.alternative.me/fng/?limit=1', timeout=5)
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
                event_dt = datetime.strptime(row['timestamp'].strip(), "%m-%d-%Y %I:%M%p").replace(tzinfo=UTC)
                diff     = event_dt - datetime.now(UTC)
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

    atr_stop  = current['ATR_14'] * RISK_CONFIG['atr_multiplier']
    threshold = get_adaptive_threshold()

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

    # 8 — OBV slope (5-candle) — skip when flat to avoid noise
    obv_slope   = df['OBV'].iloc[-1] - df['OBV'].iloc[-5]
    obv_denom   = df['volume'].iloc[-5:].sum()
    obv_rel     = abs(obv_slope) / obv_denom if obv_denom > 0 else 0
    if obv_rel >= 0.001:
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

        # 12 — BTC Dominance
        btc_dom = market_structure.get('btc_dom', {})
        if btc_dom.get('bias') == 'BULLISH':
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ BTC Dominance RISING ({btc_dom.get('current',0):.1f}%) — capital rotating into BTC")
        elif btc_dom.get('bias') == 'BEARISH':
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ BTC Dominance FALLING ({btc_dom.get('current',0):.1f}%) — capital rotating out")

        # 13 — Open Interest
        oi = market_structure.get('open_interest', {})
        if oi.get('bias') == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ Open Interest RISING ({oi.get('change_pct',0):+.2f}%) — trend confirmation")
        elif oi.get('bias') == 'BEARISH':
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ Open Interest FALLING ({oi.get('change_pct',0):+.2f}%) — positions closing")

        # 14 — Futures basis (mark vs index spread)
        basis_pct = funding.get('basis_pct', 0)
        basis_bias = funding.get('basis_bias', 'NEUTRAL')
        if basis_bias == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ Futures premium ({basis_pct:+.3f}%) — long demand")
        elif basis_bias == 'BEARISH':
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ Futures discount ({basis_pct:+.3f}%) — weak demand")

    # 15 — Stochastic RSI (reduced weight when RSI already in same extreme zone)
    sk, sd  = current.get('StochRSI_K'), current.get('StochRSI_D')
    psk, psd = previous.get('StochRSI_K'), previous.get('StochRSI_D')
    rsi_now = current.get('RSI_14', 50)
    if not any(pd.isna(v) for v in [sk, sd, psk, psd] if v is not None):
        if sk < 20 and sk > sd and psk <= psd:
            weight = 0.25 if rsi_now < 30 else 1.0
            buy_conditions += weight
            signal['reasons'].append(f"✓ StochRSI oversold crossover K={sk:.1f} — bullish momentum"
                                     + (" (RSI confirms)" if weight < 1.0 else ""))
        elif sk > 80 and sk < sd and psk >= psd:
            weight = 0.25 if rsi_now > 70 else 1.0
            sell_conditions += weight
            signal['reasons'].append(f"✗ StochRSI overbought crossover K={sk:.1f} — bearish momentum"
                                     + (" (RSI confirms)" if weight < 1.0 else ""))
        elif sk < 20:
            weight = 0.15 if rsi_now < 30 else 0.5
            buy_conditions += weight
            signal['reasons'].append(f"✓ StochRSI oversold (K={sk:.1f})"
                                     + (" (RSI confirms)" if weight < 0.5 else ""))
        elif sk > 80:
            weight = 0.15 if rsi_now > 70 else 0.5
            sell_conditions += weight
            signal['reasons'].append(f"✗ StochRSI overbought (K={sk:.1f})"
                                     + (" (RSI confirms)" if weight < 0.5 else ""))

    # 16 — Support/Resistance proximity (within 0.3%)
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

    # 17 — VWAP position
    vwap = current.get('VWAP_24')
    if vwap and not pd.isna(vwap):
        if current['close'] > vwap:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Price above VWAP ${vwap:,.2f} — institutional buying")
        else:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Price below VWAP ${vwap:,.2f} — institutional selling")

    # Determine final signal
    if buy_conditions >= threshold and buy_conditions > sell_conditions:
        signal['type']      = 'BUY'
        signal['strength']  = buy_conditions
        signal['stop_loss'] = current['close'] - atr_stop
        raw_tp = current['close'] + (atr_stop * RISK_CONFIG['take_profit_rr'])
        if sr and sr.get('resistance'):
            dist_sr = sr['resistance'] - current['close']
            if dist_sr > 0 and dist_sr < (raw_tp - current['close']) * 0.85:
                capped_tp = sr['resistance'] * 0.995
                if (capped_tp - current['close']) / atr_stop >= 1.0:
                    signal['reasons'].append(
                        f"⚠️  TP capped at resistance ${sr['resistance']:,.0f}")
                    raw_tp = capped_tp
        signal['take_profit'] = raw_tp
    elif sell_conditions >= threshold and sell_conditions > buy_conditions:
        signal['type']      = 'SELL'
        signal['strength']  = sell_conditions
        signal['stop_loss'] = current['close'] + atr_stop
        raw_tp = current['close'] - (atr_stop * RISK_CONFIG['take_profit_rr'])
        if sr and sr.get('support'):
            dist_sr = current['close'] - sr['support']
            if dist_sr > 0 and dist_sr < (current['close'] - raw_tp) * 0.85:
                capped_tp = sr['support'] * 1.005
                if (current['close'] - capped_tp) / atr_stop >= 1.0:
                    signal['reasons'].append(
                        f"⚠️  TP capped at support ${sr['support']:,.0f}")
                    raw_tp = capped_tp
        signal['take_profit'] = raw_tp
    else:
        signal['type']     = 'HOLD'
        signal['strength'] = max(buy_conditions, sell_conditions)

    signal['buy_score']  = round(buy_conditions, 2)
    signal['sell_score'] = round(sell_conditions, 2)

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
        with ThreadPoolExecutor(max_workers=9) as pool:
            futures_map['htf']         = pool.submit(get_htf_trend)
            futures_map['funding']     = pool.submit(fetch_funding_rate)
            futures_map['ls']          = pool.submit(fetch_long_short_ratio)
            futures_map['dxy']         = pool.submit(fetch_dxy_trend)
            futures_map['sp500']       = pool.submit(fetch_sp500_trend)
            futures_map['stablecoin']  = pool.submit(fetch_stablecoin_supply)
            futures_map['btc_dom']     = pool.submit(fetch_btc_dominance)
            futures_map['oi']          = pool.submit(fetch_open_interest)
            if include_news:
                futures_map['fng']     = pool.submit(fetch_fear_and_greed)

            htf = futures_map['htf'].result()
            market_structure = {
                'funding':       futures_map['funding'].result(),
                'long_short':    futures_map['ls'].result(),
                'dxy':           futures_map['dxy'].result(),
                'sp500':         futures_map['sp500'].result(),
                'stablecoin':    futures_map['stablecoin'].result(),
                'btc_dom':       futures_map['btc_dom'].result(),
                'open_interest': futures_map['oi'].result(),
            }
            fng = futures_map['fng'].result() if include_news else None

        signal = generate_signals(df, htf, market_structure, sr)

        news_data = None
        if include_news:
            logger.info("Computing combined sentiment...")
            news_data = get_combined_sentiment(fng=fng)
            signal    = integrate_news_with_signal(signal, news_data)

        display_analysis(df, signal, news_data, htf, market_structure)
        signal['db_id'] = log_signal(signal, df, htf)
        update_threshold_state(signal['type'])
        return signal

    except Exception as e:
        logger.error(f"{type(e).__name__}: {str(e)[:120]}")
        return None


# ============================================================================
# DISPLAY (terminal-formatted, color‑coded, box‑drawn)
# ============================================================================

# ANSI colour codes
_C = {
    "rst":  "\033[0m",
    "bld":  "\033[1m",
    "dim":  "\033[2m",
    "grn":  "\033[92m",
    "red":  "\033[91m",
    "yel":  "\033[93m",
    "cyn":  "\033[96m",
    "wht":  "\033[97m",
    "gry":  "\033[90m",
}

_M = 92  # total output width

def _h(heading, width=_M):
    """Medium heading."""
    print(f"\n  {_C['cyn']}{_C['bld']}{heading}{_C['rst']}")

def _kv(k, v, kw=14):
    """Key‑value line. *v* can be a (value, color_key) tuple for colouring."""
    if isinstance(v, tuple):
        v, ck = v
        colour = _C.get(ck, "")
        print(f"  {_C['dim']}{k:<{kw}}{_C['rst']} {colour}{v}{_C['rst']}")
    else:
        print(f"  {_C['dim']}{k:<{kw}}{_C['rst']} {v}")

def _bias_icon(bias):
    if bias == "BULLISH":  return f"{_C['grn']}▲{_C['rst']}"
    if bias == "BEARISH":  return f"{_C['red']}▼{_C['rst']}"
    return f"{_C['gry']}─{_C['rst']}"

def _bias_stars(bias):
    if bias == "BULLISH":  return f"{_C['grn']}★★★{_C['rst']}"
    if bias == "BEARISH":  return f"{_C['red']}★★★{_C['rst']}"
    return "───"

def _colour_for(value, green_above=0, red_below=0):
    """Return colour key for a numeric *value*."""
    if value > green_above: return "grn"
    if value < red_below:   return "red"
    return ""

def _signal_box(signal, effective_threshold):
    """Draw the prominent signal‑verdict box."""
    stype = signal["type"]
    colors = {"BUY": "grn", "SELL": "red", "HOLD": "yel"}
    c = colors.get(stype, "yel")
    icons = {"BUY": "▲", "SELL": "▼", "HOLD": "─"}
    icon = icons.get(stype, "─")

    l1 = f"  {_C[c]}{_C['bld']}{icon} {stype}{_C['rst']}"
    l1 += f"   Strength  {_C['bld']}{signal['strength']:.2f}{_C['rst']} / {SIGNAL_MAX_SCORE}"
    l1 += f"   Threshold  {_C['dim']}{effective_threshold:.2f}{_C['rst']}"

    if effective_threshold != SIGNAL_THRESHOLD:
        l1 += f"  {_C['yel']}(adaptive){_C['rst']}"

    print(f"\n╭{'─' * (_M - 2)}╮")
    print(f"│ {l1:<{_M - 4}} │")
    print(f"╰{'─' * (_M - 2)}╯")

    if stype != "HOLD" and signal.get("stop_loss"):
        entry  = signal["entry_price"]
        sl     = signal["stop_loss"]
        tp     = signal["take_profit"]
        sl_pct = abs(entry - sl) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100
        rr     = tp_pct / sl_pct if sl_pct > 0 else 0
        l2 = (
            f"  Entry ${entry:,.0f}  "
            f"│  SL ${sl:,.0f}  ({sl_pct:.2f}%)  "
            f"│  TP ${tp:,.0f}  (+{tp_pct:.2f}%)  "
            f"│  R/R 1:{rr:.2f}"
        )
        print(f"  {_C['dim']}{l2}{_C['rst']}")


def display_analysis(df, signal, news_data, htf=None, market_structure=None):
    last = df.iloc[-1]
    sr = signal.get("support_resistance", {})
    effective_threshold = get_adaptive_threshold()

    # ── header ────────────────────────────────────────────────────
    print(f"\n{_C['dim']}╭{'─' * (_M - 2)}╮{_C['rst']}")
    title = f"SpotSignal · BTC/USDT · 1H"
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{_C['dim']}│{_C['rst']} {_C['bld']}{_C['wht']}{title:^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}│{_C['rst']} {_C['gry']}{time_str:^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}│{_C['rst']} {_C['gry']}{'hobby · study · experiment — not financial advice · have fun':^{_M - 4}}{_C['dim']} │{_C['rst']}")
    print(f"{_C['dim']}╰{'─' * (_M - 2)}╯{_C['rst']}")

    # ── signal verdict ────────────────────────────────────────────
    _signal_box(signal, effective_threshold)

    # ── PRICE & TREND ───────────────────────────────────────────────
    col_w = _M - 6
    sep   = f"  {'─' * col_w}"
    trend = f"{_C['grn']}▲ BULLISH{_C['rst']}" if last["close"] > last["EMA_200"] else f"{_C['red']}▼ BEARISH{_C['rst']}"

    print()
    print(f"  {_C['bld']}PRICE & TREND{_C['rst']}")
    print(sep)
    hi24, lo24 = df["high"].tail(24).max(), df["low"].tail(24).min()
    _kv("Price",        f"${last['close']:,.0f}")
    _kv("EMA 200",      f"${last['EMA_200']:,.0f}  {trend}")
    _kv("24h High",     f"${hi24:,.0f}")
    _kv("24h Low",      f"${lo24:,.0f}")
    if sr.get("resistance"):
        _kv("Resistance", f"${sr['resistance']:,.0f}")
    if sr.get("support"):
        _kv("Support",    f"${sr['support']:,.0f}")

    # ── MULTI-TIMEFRAME ────────────────────────────────────────────
    if htf:
        print()
        print(f"  {_C['bld']}MULTI-TIMEFRAME{_C['rst']}")
        print(sep)
        a = f"{_C['grn']}✓ ALIGNED{_C['rst']}" if htf["aligned"] else f"{_C['red']}✗ DIVERGING{_C['rst']}"
        _kv("4H",          htf["4h"])
        _kv("1D",          htf["1d"])
        _kv("Alignment",   a)

    # ── MARKET STRUCTURE ───────────────────────────────────────────
    if market_structure:
        funding = market_structure.get("funding", {})
        ls = market_structure.get("long_short", {})
        dxy = market_structure.get("dxy", {})

        print()
        print(f"  {_C['bld']}MARKET STRUCTURE{_C['rst']}")
        print(sep)
        _kv("Funding",
            f"{funding.get('rate_pct',0):+.5f}%  {_bias_icon(funding.get('bias',''))}")
        _kv("L/S Ratio",
            f"{ls.get('ratio',1):.2f}       {_bias_icon(ls.get('bias',''))}")
        _kv("DXY",
            f"{dxy.get('current',0):.3f}  ({dxy.get('change_pct',0):+.2f}%)")
        sp500 = market_structure.get("sp500", {})
        if sp500.get("current"):
            _kv("S&P 500",
                f"{sp500['current']:,.0f}  ({sp500['change_pct']:+.2f}%)  {_bias_icon(sp500.get('bias',''))}")
        stable = market_structure.get("stablecoin", {})
        if stable.get("total_b"):
            _kv("Stablecoin",
                f"${stable['total_b']:.0f}B  {_bias_icon(stable.get('bias',''))}")
        btc_dom = market_structure.get("btc_dom", {})
        if btc_dom.get("current"):
            _kv("BTC Dominance",
                f"{btc_dom['current']:.1f}%  {_bias_icon(btc_dom.get('bias',''))}")
        oi = market_structure.get("open_interest", {})
        if oi.get("notional"):
            _kv("Open Interest",
                f"${oi['notional']/1e9:.2f}B  ({oi['change_pct']:+.3f}%)  {_bias_icon(oi.get('bias',''))}")
        basis_pct = funding.get("basis_pct", 0)
        _kv("Futures Basis",
            f"{basis_pct:+.4f}%  {_bias_icon(funding.get('basis_bias','NEUTRAL'))}")

    # ── TECHNICALS ─────────────────────────────────────────────────
    print()
    print(f"  {_C['bld']}TECHNICALS{_C['rst']}")
    print(sep)
    rsi_val = last["RSI_14"]
    rsi_tag = f" {_C['red']}OB{_C['rst']}" if rsi_val > 70 else (f" {_C['grn']}OS{_C['rst']}" if rsi_val < 30 else "")
    _kv("RSI 14", f"{rsi_val:.1f}{rsi_tag}")
    macd_tag = f"{_C['grn']}▲{_C['rst']}" if last["MACD"] > last["MACD_Signal"] else f"{_C['red']}▼{_C['rst']}"
    _kv("MACD",  f"{last['MACD']:,.0f}  {macd_tag}")
    sk = last.get("StochRSI_K")
    if sk is not None and not pd.isna(sk):
        sd = last.get("StochRSI_D", 0)
        sk_tag = f" {_C['red']}OB{_C['rst']}" if sk > 80 else (f" {_C['grn']}OS{_C['rst']}" if sk < 20 else "")
        _kv("StochRSI K/D", f"{sk:.1f} / {sd:.1f}{sk_tag}")
    vwap_v = last.get("VWAP_24")
    if vwap_v and not pd.isna(vwap_v):
        vw_tag = f"{_C['grn']}▲{_C['rst']}" if last["close"] > vwap_v else f"{_C['red']}▼{_C['rst']}"
        _kv("VWAP 24h", f"${vwap_v:,.0f}  {vw_tag}")
    _kv("BB Upper",  f"${last['BB_Upper']:,.0f}")
    _kv("BB Middle", f"${last['BB_Middle']:,.0f}")
    _kv("BB Lower",  f"${last['BB_Lower']:,.0f}")
    _kv("ATR 14",    f"${last['ATR_14']:,.0f}")
    obv_slope = df["OBV"].iloc[-1] - df["OBV"].iloc[-5]
    obv_tag = f"{_C['grn']}▲{_C['rst']}" if obv_slope > 0 else f"{_C['red']}▼{_C['rst']}"
    _kv("OBV (5c)", f"{obv_slope:+,.0f}  {obv_tag}")
    div = signal.get("rsi_divergence", "NONE")
    div_str = {"BULLISH": f"{_C['grn']}▲ BULL{_C['rst']}", "BEARISH": f"{_C['red']}▼ BEAR{_C['rst']}"}.get(div, "─")
    _kv("RSI Diverg", div_str)

    # ── SENTIMENT ──────────────────────────────────────────────────
    if news_data:
        print()
        print(f"  {_C['bld']}SENTIMENT{_C['rst']}")
        print(sep)
        if news_data.get("fear_greed"):
            fng = news_data["fear_greed"]
            val = fng["value"]
            bar_f = min(val // 10, 10)
            bar = f"{_C['grn']}{'█' * bar_f}{_C['gry']}{'░' * (10 - bar_f)}{_C['rst']}"
            _kv("F&G Index", f"[{bar}] {val}/100  {fng.get('label','')}")
        sent = signal.get("news_sentiment", "NEUTRAL")
        conf = signal.get("news_confidence", 0)
        sent_col = "grn" if sent == "BULLISH" else ("red" if sent == "BEARISH" else "dim")
        _kv("Sentiment", f"{_C[sent_col]}{sent}{_C['rst']}  ({conf:.0f}% confidence)")
        sources = ", ".join(news_data.get("sources_checked", []))
        _kv("Sources", f"{_C['dim']}{sources[:70]}{_C['rst']}")

        headlines = news_data.get("headlines", [])
        if headlines:
            print()
            print(f"  {_C['bld']}TOP HEADLINES{_C['rst']}")
            print(sep)
            for i, h in enumerate(headlines[:6], 1):
                icon = f"{_C['grn']}▲{_C['rst']}" if h["sentiment"] > 0 else (f"{_C['red']}▼{_C['rst']}" if h["sentiment"] < 0 else "─")
                cat = "G" if h.get("category") == "geopolitical" else "C"
                print(f"  {i}. {cat} {icon} {h['title'][:75]}")

    # ── SIGNAL REASONS (grouped) ───────────────────────────────────
    reasons = signal.get("reasons", [])
    if reasons:
        print()
        print(f"  {_C['bld']}SIGNAL REASONS{_C['rst']} ({len(reasons)} of 17)")
        print(sep)

        groups = {"trend": [], "momentum": [], "volume": [], "structure": [], "macro": [], "other": []}
        for r in reasons:
            r_lower = r.lower()
            if any(w in r_lower for w in ("ema", "htf", "vwap", "support", "resistance")):
                groups["trend"].append(r)
            elif any(w in r_lower for w in ("rsi", "macd", "stoch", "divergence", "bollinger", "band")):
                groups["momentum"].append(r)
            elif any(w in r_lower for w in ("volume", "obv")):
                groups["volume"].append(r)
            elif any(w in r_lower for w in ("funding", "l/s", "dxy", "basis", "open interest", "liquidat")):
                groups["structure"].append(r)
            elif any(w in r_lower for w in ("s&p", "stablecoin", "dominance", "macro", "forced hold")):
                groups["macro"].append(r)
            else:
                groups["other"].append(r)

        for cat, label in [("trend", "TREND"), ("momentum", "MOMENTUM"),
                            ("volume", "VOLUME"), ("structure", "STRUCTURE"),
                            ("macro", "MACRO"), ("other", "OTHER")]:
            if groups[cat]:
                print(f"  {_C['bld']}{label}{_C['rst']}")
                for item in groups[cat]:
                    stripped = item[2:] if item[:1] in "✓✗⚠" else item[1:] if item[:1] in "─→" else item
                    icon = "✓" if item.startswith("✓") else ("✗" if item.startswith("✗") else "•")
                    icol = _C["grn"] if item.startswith("✓") else (_C["red"] if item.startswith("✗") else _C["yel"])
                    print(f"    {icol}{icon}{_C['rst']} {stripped[:70]}")

    # ── PERFORMANCE ────────────────────────────────────────────────
    try:
        import signal_history as _sh
        wr = _sh.get_win_rate()
        pf = _sh.get_profit_factor()
        total_pnl, count, avg = _sh.get_closed_pnl()
        if count > 0:
            print()
            print(f"  {_C['bld']}PERFORMANCE{_C['rst']} (all‑time paper)")
            print(sep)
            wr_str = f"{wr:.1%}" if wr is not None else "—"
            pf_str = f"{pf:.2f}" if pf is not None else "—"
            pnl_col = "grn" if total_pnl > 0 else "red"
            _kv("Trades",       str(count))
            _kv("Win Rate",     wr_str)
            _kv("Profit Factor", pf_str)
            _kv("Net P&L",      f"{_C[pnl_col]}{total_pnl:+.2f}%{_C['rst']}")
    except Exception:
        pass

    # ── POSITION SIZING (always shown) ─────────────────────────────
    buy_s  = signal.get("buy_score", 0)
    sell_s = signal.get("sell_score", 0)
    direction = "BUY" if buy_s > sell_s else ("SELL" if sell_s > buy_s else "neutral")

    pos = calculate_position_size(signal)
    is_active = signal["type"] != "HOLD" and signal.get("stop_loss")
    futures = calculate_futures_position(signal) if is_active else None
    entry_px = signal.get("entry_price", last["close"])
    atr = last.get("ATR_14", 0)

    # Simulation balances
    spot_start     = RISK_CONFIG["account_balance"]
    futures_start  = FUTURES_CONFIG["futures_balance"]
    try:
        total_pnl_pct, closed_count, avg_pnl = _sh.get_closed_pnl()
    except Exception:
        total_pnl_pct, closed_count, avg_pnl = 0, 0, 0
    spot_pnl_pct    = total_pnl_pct * 0.6
    futures_pnl_pct = total_pnl_pct * 0.4
    spot_now        = spot_start    * (1 + spot_pnl_pct    / 100)
    futures_now     = futures_start * (1 + futures_pnl_pct / 100)

    print()
    print(f"  {_C['bld']}POSITION SIZING{_C['rst']}")
    print(sep)
    _kv("Entry Price", f"${entry_px:,.0f}" if is_active else f"${last['close']:,.0f}")

    # -- SPOT --------------------------------------------------------
    print(f"  {_C['bld']}SPOT{_C['rst']}  ──  {_C['dim']}Balance  ${spot_start:,.0f}{_C['rst']}", end="")
    if closed_count > 0:
        pnl_col = "grn" if spot_pnl_pct >= 0 else "red"
        print(f"  {_C['dim']}→  now {_C[pnl_col]}${spot_now:,.0f}{_C['rst']}  {_C[('dim' if abs(spot_pnl_pct) < 1 else pnl_col)]}({spot_pnl_pct:+.1f}%){_C['rst']}")
    else:
        print()
    if is_active:
        sl = signal["stop_loss"]
        tp = signal["take_profit"]
        sl_pct = abs(entry_px - sl) / entry_px * 100
        tp_pct = abs(tp - entry_px) / entry_px * 100
        rr = tp_pct / sl_pct if sl_pct > 0 else 0
        _kv("  Stop Loss",   f"${sl:,.0f}  (-{sl_pct:.2f}%)")
        _kv("  Take Profit", f"${tp:,.0f}  (+{tp_pct:.2f}%)")
        _kv("  Risk/Reward", f"1:{rr:.2f}")
        _kv("  Position",    f"${pos['usdt_amount']:,.0f}  ({pos['position_ratio']:.1f}% of balance)")
        _kv("  Max Risk",    f"${pos['risk_amount']:,.0f}  ({RISK_CONFIG['risk_per_trade']*100:.0f}% per trade)")
    else:
        if atr > 0 and direction != "neutral":
            sl_dist = atr * RISK_CONFIG["atr_multiplier"]
            tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
            if direction == "BUY":
                hypo_sl = last["close"] - sl_dist
                hypo_tp = last["close"] + tp_dist
            else:
                hypo_sl = last["close"] + sl_dist
                hypo_tp = last["close"] - tp_dist
            _kv("  Stop Loss",   f"{_C['gry']}~${hypo_sl:,.0f} (if fired){_C['rst']}")
            _kv("  Take Profit", f"{_C['gry']}~${hypo_tp:,.0f} (if fired){_C['rst']}")
            _kv("  Risk/Reward", f"{_C['gry']}1:{RISK_CONFIG['take_profit_rr']:.1f}{_C['rst']}")
        else:
            _kv("  Stop Loss",   "-")
            _kv("  Take Profit", "-")
            _kv("  Risk/Reward", "-")
        _kv("  Position",   f"{_C['gry']}—  (no trade){_C['rst']}")
        _kv("  Max Risk",   f"${spot_start * RISK_CONFIG['risk_per_trade']:,.0f}  ({RISK_CONFIG['risk_per_trade']*100:.0f}% per trade)")

    # -- FUTURES -----------------------------------------------------
    print(f"  {_C['bld']}FUTURES{_C['rst']}  ──  {_C['dim']}Balance  ${futures_start:,.0f}{_C['rst']}", end="")
    if closed_count > 0:
        pnl_col = "grn" if futures_pnl_pct >= 0 else "red"
        print(f"  {_C['dim']}→  now {_C[pnl_col]}${futures_now:,.0f}{_C['rst']}  {_C[('dim' if abs(futures_pnl_pct) < 1 else pnl_col)]}({futures_pnl_pct:+.1f}%){_C['rst']}")
    else:
        print()
    if is_active:
        sl = signal["stop_loss"]
        tp = signal["take_profit"]
        sl_pct = abs(entry_px - sl) / entry_px * 100
        tp_pct = abs(tp - entry_px) / entry_px * 100
        rr = tp_pct / sl_pct if sl_pct > 0 else 0
        _kv("  Stop Loss",   f"${sl:,.0f}  (-{sl_pct:.2f}%)")
        _kv("  Take Profit", f"${tp:,.0f}  (+{tp_pct:.2f}%)")
        _kv("  Risk/Reward", f"1:{rr:.2f}")
        if futures:
            _kv("  Direction",  f"{futures['direction']}")
            _kv("  Leverage",   f"{futures['leverage']}x  [{futures['tier']}]")
            _kv("  Margin",     f"${futures['margin']:,.0f}  ({futures['margin_pct']:.1f}% of balance)")
            _kv("  Pos. Value", f"${futures['position_value']:,.0f}")
            _kv("  Liquidation",f"${futures['liquidation_price']:,.0f}")
            _kv("  Max Risk",   f"${futures['risk_amount']:,.0f}  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}% per trade)")
        else:
            _kv("  Direction",  "-")
            _kv("  Leverage",   "-")
            _kv("  Margin",     "-")
            _kv("  Liquidation","-")
            _kv("  Max Risk",   "-")
    else:
        if atr > 0 and direction != "neutral":
            sl_dist = atr * RISK_CONFIG["atr_multiplier"]
            tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
            if direction == "BUY":
                hypo_sl = last["close"] - sl_dist
                hypo_tp = last["close"] + tp_dist
            else:
                hypo_sl = last["close"] + sl_dist
                hypo_tp = last["close"] - tp_dist
            _kv("  Stop Loss",   f"{_C['gry']}~${hypo_sl:,.0f} (if fired){_C['rst']}")
            _kv("  Take Profit", f"{_C['gry']}~${hypo_tp:,.0f} (if fired){_C['rst']}")
            _kv("  Risk/Reward", f"{_C['gry']}1:{RISK_CONFIG['take_profit_rr']:.1f}{_C['rst']}")
        else:
            _kv("  Stop Loss",   "-")
            _kv("  Take Profit", "-")
            _kv("  Risk/Reward", "-")
        _kv("  Direction",   "-")
        _kv("  Leverage",    "-")
        _kv("  Margin",      "-")
        _kv("  Liquidation", "-")
        _kv("  Max Risk",    f"${futures_start * FUTURES_CONFIG['risk_per_trade']:,.0f}  ({FUTURES_CONFIG['risk_per_trade']*100:.0f}% per trade)")

    # ── NOTE ───────────────────────────────────────────────────────
    gap = effective_threshold - max(buy_s, sell_s)

    print()
    print(f"  {_C['bld']}NOTE{_C['rst']}")
    print(sep)

    # Score breakdown
    b_bar = min(int(buy_s / max(buy_s + sell_s, 1) * 12), 12)
    s_bar = 12 - b_bar
    print(f"  Buy      {_C['grn']}{'█' * b_bar}{_C['gry']}{'░' * (12 - b_bar)}{_C['rst']}  {buy_s:.2f}")
    print(f"  Sell     {_C['red']}{'█' * s_bar}{_C['gry']}{'░' * (12 - s_bar)}{_C['rst']}  {sell_s:.2f}")
    print(f"  ─ {'─' * 20}")
    print(f"  {'Threshold:':<14} {_C['dim']}{effective_threshold:.2f}{_C['rst']}  (needed to fire)")

    if signal["type"] == "HOLD":
        dir_col = "grn" if direction == "BUY" else ("red" if direction == "SELL" else "dim")
        print(f"  {'Direction:':<14} {_C[dir_col]}{direction.upper()}{_C['rst']} leads by {abs(buy_s - sell_s):.2f}")
        if gap > 0:
            print(f"  {'Gap to fire:':<14} {_C['yel']}{gap:.2f}{_C['rst']}  (needs ~{max(1, int(gap / 0.5 + 0.5))} more conditions)")
        else:
            print(f"  {'Gap to fire:':<14} {_C['grn']}READY{_C['rst']} but sell side {_C['red']}overrides{_C['rst']}")
        if effective_threshold != SIGNAL_THRESHOLD:
            print(f"  {'':<14} {_C['yel']}adaptive threshold active (base={SIGNAL_THRESHOLD}){_C['rst']}")
    else:
        _kv("Direction", f"{_C['grn'] if signal['type'] == 'BUY' else _C['red']}{signal['type']}{_C['rst']}")
        _kv("Strength", f"{signal['strength']:.2f} / {SIGNAL_MAX_SCORE}")

    # Paper P&L note
    try:
        _, count, avg_pnl = _sh.get_closed_pnl()
        if count > 0:
            pnl_col = "grn" if avg_pnl > 0 else "red"
            print(f"  {'Paper P&L:':<14} {_C['dim']}{count} closed trades{_C['rst']}  avg {_C[pnl_col]}{avg_pnl:+.2f}%{_C['rst']}")
    except Exception:
        pass

    # ── VERDICT (plain‑English for new traders) ────────────────────
    print()
    print(f"  {_C['bld']}WHAT THIS MEANS{_C['rst']}")
    print(sep)

    if signal["type"] == "BUY":
        print(f"  {_C['grn']}▶ OPEN A LONG{_C['rst']} — indicators agree on upward move.")
        print(f"  {_C['dim']}  Buy spot or open a futures long.  Use the SL and TP above.{_C['rst']}")
    elif signal["type"] == "SELL":
        print(f"  {_C['red']}▶ OPEN A SHORT{_C['rst']} — indicators agree on downward move.")
        print(f"  {_C['dim']}  Short spot or open a futures short.  Use the SL and TP above.{_C['rst']}")
    else:
        if direction == "BUY":
            print(f"  {_C['yel']}■ WAIT{_C['rst']} — bullish signals are building, but not enough to buy yet.")
        elif direction == "SELL":
            print(f"  {_C['yel']}■ WAIT{_C['rst']} — bearish signals are building, but not enough to sell yet.")
        else:
            print(f"  {_C['yel']}■ WAIT{_C['rst']} — market is neutral.  No clear direction.")
        print(f"  {_C['dim']}  The bot waits for strong agreement before risking capital.{_C['rst']}")
        if gap > 0:
            print(f"  {_C['dim']}  Need {_C['yel']}~{gap:.1f}{_C['dim']} more points to fire a {direction.upper()} signal.{_C['rst']}")

    # ── INDICATOR GUIDE (compact) ──────────────────────────────────
    print()
    print(f"  {_C['bld']}INDICATOR GUIDE{_C['rst']} {_C['dim']}(what each number means){_C['rst']}")
    print(sep)

    guides = [
        ("Price > EMA200", "Long-term trend is up.  BTC in a bull market.",
         last["close"] > last["EMA_200"]),
        ("RSI", f"{last['RSI_14']:.0f}.  Over 70 = overbought (due for pullback).  Under 30 = oversold (bounce likely).",
         last["RSI_14"] < 70 and last["RSI_14"] > 30),
        ("MACD", "Short-term momentum.  Bullish cross = trend starting up.  Bearish = losing steam.",
         last["MACD"] > last["MACD_Signal"]),
        ("HTF Diverging", "4H and 1D charts disagree.  Market is uncertain — expect chop.",
         htf and not htf.get("aligned", True)),
        ("L/S Ratio 0.60", "More shorts than longs.  If price goes up, shorts get squeezed = fast pump.",
         True),
        ("Funding flat", "No one is paying to hold positions.  No extreme leverage either way.",
         abs(funding.get("rate_pct", 0)) < 0.01) if market_structure else ("", "", False),
        ("S&P 500 flat", "Equities are sideways.  BTC usually follows equities sentiment.",
         abs(sp500.get("change_pct", 0)) < 0.5) if market_structure and sp500.get("current") else ("", "", False),
        ("Fear & Greed 47", "Neutral sentiment.  Extreme fear (<20) is often a buy signal.  Extreme greed (>80) is often a sell signal.",
         news_data and news_data.get("fear_greed", {}).get("value", 50) < 60 and news_data.get("fear_greed", {}).get("value", 50) > 40),
    ]

    for label, explanation, active in guides:
        if not label:
            continue
        if active:
            print(f"  {_C['grn']}▶{_C['rst']} {_C['bld']}{label}{_C['rst']} — {_C['dim']}{explanation}{_C['rst']}")
        else:
            print(f"  {_C['gry']}·{_C['rst']} {_C['bld']}{label}{_C['rst']} — {_C['gry']}{explanation}{_C['rst']}")

    print()


# ============================================================================
# STANDALONE ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                        stream=sys.stdout)
    analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True)
