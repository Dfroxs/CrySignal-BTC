"""Futures (1H) analysis pipeline — 18 conditions, LONG/SHORT, HTF=4H+1D."""

import logging
from concurrent.futures import ThreadPoolExecutor

from config import LEVERAGE_CONFIG

from trading.history import log_cycle, log_signal
from signals.ohlcv import fetch_ohlcv_df
from signals.htf import get_htf_trend
from signals.sentiment import get_combined_sentiment
from signals.engine import generate_signals, integrate_news_with_signal
from signals.indicators import detect_support_resistance
from signals.market_data import (
    fetch_btc_dominance,
    fetch_dxy_trend,
    fetch_fear_and_greed,
    fetch_funding_rate,
    fetch_gold_price,
    fetch_long_short_ratio,
    fetch_open_interest,
    fetch_sp500_trend,
    fetch_stablecoin_supply,
    fetch_taker_buy_sell_ratio,
    fetch_vix,
    get_adaptive_threshold,
    update_threshold_state,
)
from signals.terminal import display_analysis

logger = logging.getLogger(__name__)


def analyze_futures_signal(symbol='BTC/USDT', include_news=True, display=False):
    """Full 1H futures pipeline: 18 conditions including funding/L/S/OI/basis."""
    logger.info("[FUTURES] Analyzing %s (1H)...", symbol)
    try:
        df = fetch_ohlcv_df(symbol, '1h', limit=500, vwap_period=24)
        sr = detect_support_resistance(df)

        logger.info("Fetching futures market data in parallel...")
        fmap = {}
        with ThreadPoolExecutor(max_workers=11) as pool:
            fmap['htf'] = pool.submit(get_htf_trend)
            fmap['funding'] = pool.submit(fetch_funding_rate)
            fmap['ls'] = pool.submit(fetch_long_short_ratio)
            fmap['taker'] = pool.submit(fetch_taker_buy_sell_ratio)
            fmap['dxy'] = pool.submit(fetch_dxy_trend)
            fmap['sp500'] = pool.submit(fetch_sp500_trend)
            fmap['stablecoin'] = pool.submit(fetch_stablecoin_supply)
            fmap['btc_dom'] = pool.submit(fetch_btc_dominance)
            fmap['oi'] = pool.submit(fetch_open_interest)
            fmap['gold'] = pool.submit(fetch_gold_price)
            fmap['vix'] = pool.submit(fetch_vix)
            if include_news:
                fmap['fng'] = pool.submit(fetch_fear_and_greed)

            htf = fmap['htf'].result()
            market_structure = {
                'funding': fmap['funding'].result(),
                'long_short': fmap['ls'].result(),
                'taker': fmap['taker'].result(),
                'dxy': fmap['dxy'].result(),
                'sp500': fmap['sp500'].result(),
                'stablecoin': fmap['stablecoin'].result(),
                'btc_dom': fmap['btc_dom'].result(),
                'open_interest': fmap['oi'].result(),
                'gold': fmap['gold'].result(),
                'vix': fmap['vix'].result(),
            }
            fng = fmap['fng'].result() if include_news else None

        threshold = get_adaptive_threshold()
        signal = generate_signals(df, htf, market_structure, sr, mode='futures', threshold_override=threshold)

        news_data = None
        if include_news:
            logger.info("Computing combined sentiment...")
            news_data = get_combined_sentiment(fng=fng)
            signal = integrate_news_with_signal(signal, news_data)

        if display:
            display_analysis(df, signal, news_data, htf, market_structure, timeframe='1H', mode='futures')
        signal['db_id'] = log_signal(signal, df, htf)
        update_threshold_state(signal['type'])

        signal['mode'] = 'futures'
        signal['_threshold'] = threshold
        log_cycle(signal, df, market_structure, htf, 'futures')
        # ATR percentile for dynamic leverage
        from signals.indicators import compute_atr_percentile
        signal['_atr_percentile'] = round(
            compute_atr_percentile(df, LEVERAGE_CONFIG["atr_lookback"]), 3
        )
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
            'bb_middle': last.get('BB_Middle', 0),
            'bb_lower': last.get('BB_Lower', 0),
            'atr': last.get('ATR_14', 0),
            'obv_slope': df['OBV'].iloc[-1] - df['OBV'].iloc[-5],
            'hi24': df['high'].tail(24).max(),
            'lo24': df['low'].tail(24).min(),
            'mfi': last.get('MFI_14'),
            'cmf': last.get('CMF_20'),
        }
        return signal

    except Exception as e:
        logger.error("[FUTURES] %s: %s", type(e).__name__, str(e)[:120])
        return None
