"""Spot (4H) analysis pipeline — 15 conditions, BUY-only, HTF=1D+1W."""

import logging
from concurrent.futures import ThreadPoolExecutor

from config import SPOT_THRESHOLD, SPOT_MAX_SCORE
from signal_history import log_cycle, log_signal
from ohlcv import fetch_ohlcv_df
from htf import get_spot_htf_trend
from sentiment import get_combined_sentiment
from signal_engine import generate_signals, integrate_news_with_signal
from indicators import detect_support_resistance
from market_data import (
    fetch_btc_dominance,
    fetch_dxy_trend,
    fetch_fear_and_greed,
    fetch_sp500_trend,
    fetch_stablecoin_supply,
    get_spot_adaptive_threshold,
    update_spot_threshold_state,
)
from terminal import display_analysis

logger = logging.getLogger(__name__)


def analyze_spot_signal(symbol='BTC/USDT', include_news=True):
    """Full 4H spot pipeline: 15 conditions (no funding/L/S/OI/basis)."""
    logger.info("[SPOT] Analyzing %s (4H)...", symbol)
    try:
        # 6 x 4H candles = 1 trading day
        df = fetch_ohlcv_df(symbol, '4h', limit=500, vwap_period=6)
        sr = detect_support_resistance(df)

        logger.info("Fetching spot market data in parallel...")
        fmap = {}
        with ThreadPoolExecutor(max_workers=7) as pool:
            fmap['htf'] = pool.submit(get_spot_htf_trend)
            fmap['dxy'] = pool.submit(fetch_dxy_trend)
            fmap['sp500'] = pool.submit(fetch_sp500_trend)
            fmap['stablecoin'] = pool.submit(fetch_stablecoin_supply)
            fmap['btc_dom'] = pool.submit(fetch_btc_dominance)
            if include_news:
                fmap['fng'] = pool.submit(fetch_fear_and_greed)

            htf = fmap['htf'].result()
            market_structure = {
                'dxy': fmap['dxy'].result(),
                'sp500': fmap['sp500'].result(),
                'stablecoin': fmap['stablecoin'].result(),
                'btc_dom': fmap['btc_dom'].result(),
            }
            fng = fmap['fng'].result() if include_news else None

        threshold = get_spot_adaptive_threshold()
        signal = generate_signals(df, htf, market_structure, sr, mode='spot', threshold_override=threshold)

        news_data = None
        if include_news:
            logger.info("Computing spot combined sentiment...")
            news_data = get_combined_sentiment(fng=fng)
            signal = integrate_news_with_signal(signal, news_data)

        display_analysis(df, signal, news_data, htf, market_structure, timeframe='4H', mode='spot')
        signal['db_id'] = log_signal(signal, df, htf)
        update_spot_threshold_state(signal['type'])

        signal['mode'] = 'spot'
        signal['_threshold'] = threshold
        log_cycle(signal, df, market_structure, htf, 'spot')
        signal['_htf'] = htf
        signal['_market'] = market_structure
        signal['_news_data'] = news_data
        last = df.iloc[-1]
        signal['_last'] = {
            'close': last['close'],
            'ema200': last.get('EMA_200', 0),
            'rsi': last.get('RSI_14', 0),
            'macd': last.get('MACD', 0),
            'macd_sig': last.get('MACD_Signal', 0),
            'stoch_k': last.get('StochRSI_K'),
            'stoch_d': last.get('StochRSI_D'),
            'vwap': last.get('VWAP_24'),
            'bb_upper': last.get('BB_Upper', 0),
            'bb_lower': last.get('BB_Lower', 0),
            'atr': last.get('ATR_14', 0),
            'obv_slope': df['OBV'].iloc[-1] - df['OBV'].iloc[-5],
            'hi24': df['high'].tail(6).max(),
            'lo24': df['low'].tail(6).min(),
        }
        return signal

    except Exception as e:
        logger.error("[SPOT] %s: %s", type(e).__name__, str(e)[:120])
        return None
