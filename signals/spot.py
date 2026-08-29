"""Spot (4H) analysis pipeline — 15 conditions, BUY-only, HTF=1D+1W."""

import copy
import logging
from concurrent.futures import ThreadPoolExecutor

from trading.history import log_cycle, log_signal
from signals.ohlcv import fetch_ohlcv_df
from signals.htf import get_spot_htf_trend
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
    fetch_sp500_trend,
    fetch_stablecoin_supply,
    fetch_vix,
    get_spot_adaptive_threshold,
    update_spot_threshold_state,
)
from signals.terminal import display_analysis

logger = logging.getLogger(__name__)

# Cache per 4H candle — avoids re-running identical analysis 3× between 4H closes
_spot_cache = {"timestamp": 0, "signal": None}


def analyze_spot_signal(symbol='BTC/USDT', include_news=True, display=False):
    """Full 4H spot pipeline: 15 core conditions + funding/L/S futures sentiment at ½-weight (OI/basis skipped)."""
    import time as _time
    candle_ts = int(_time.time() // (4 * 3600))  # current 4H candle ID

    if _spot_cache["timestamp"] == candle_ts and _spot_cache["signal"] is not None:
        logger.info("[SPOT] Cache hit — reusing 4H candle %d result", candle_ts)
        cached = copy.deepcopy(_spot_cache["signal"])
        # Flagged so Phase 3 never OPENS from a replayed analysis: on the 2nd–4th
        # cycle of a 4H candle (--loop 60) entry_price and every gate input are up
        # to 3h old. Display, notifications and exit management still use it —
        # exits run off the live price fetched in run_cycle(), not off this dict.
        cached['_cached'] = True
        return cached

    logger.info("[SPOT] Analyzing %s (4H)...", symbol)
    try:
        # 6 x 4H candles = 1 trading day
        df = fetch_ohlcv_df(symbol, '4h', limit=500, vwap_period=6)
        sr = detect_support_resistance(df)

        logger.info("Fetching spot market data in parallel...")
        fmap = {}
        with ThreadPoolExecutor(max_workers=11) as pool:
            fmap['htf'] = pool.submit(get_spot_htf_trend)
            fmap['dxy'] = pool.submit(fetch_dxy_trend)
            fmap['sp500'] = pool.submit(fetch_sp500_trend)
            fmap['stablecoin'] = pool.submit(fetch_stablecoin_supply)
            fmap['btc_dom'] = pool.submit(fetch_btc_dominance)
            fmap['funding'] = pool.submit(fetch_funding_rate)
            fmap['ls'] = pool.submit(fetch_long_short_ratio)
            fmap['gold'] = pool.submit(fetch_gold_price)
            fmap['vix'] = pool.submit(fetch_vix)
            if include_news:
                fmap['fng'] = pool.submit(fetch_fear_and_greed)

            htf = fmap['htf'].result()
            market_structure = {
                'dxy': fmap['dxy'].result(),
                'sp500': fmap['sp500'].result(),
                'stablecoin': fmap['stablecoin'].result(),
                'btc_dom': fmap['btc_dom'].result(),
                'funding': fmap['funding'].result(),
                'long_short': fmap['ls'].result(),
                'gold': fmap['gold'].result(),
                'vix': fmap['vix'].result(),
            }
            fng = fmap['fng'].result() if include_news else None

        threshold = get_spot_adaptive_threshold()
        signal = generate_signals(df, htf, market_structure, sr, mode='spot', threshold_override=threshold)

        news_data = None
        if include_news:
            logger.info("Computing spot combined sentiment...")
            news_data = get_combined_sentiment(fng=fng)
            signal = integrate_news_with_signal(signal, news_data, htf)

        if display:
            display_analysis(df, signal, news_data, htf, market_structure, timeframe='4H', mode='spot')
        signal['db_id'] = log_signal(signal, df, htf)
        update_spot_threshold_state(signal['type'])

        signal['mode'] = 'spot'
        # `_threshold` is left as generate_signals() set it: the EFFECTIVE
        # threshold, session + regime bumps included — the bar this signal
        # actually had to clear. Overwriting it with the raw adaptive base made
        # sizing (_compute_confidence) and run_bot._check_reentry_quality
        # measure strength against a different number than the engine gated on.
        log_cycle(signal, df, market_structure, htf, 'spot')
        from signals.indicators import compute_atr_percentile
        signal['_atr_percentile'] = round(compute_atr_percentile(df), 3)
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
            'hi24': df['high'].tail(6).max(),
            'lo24': df['low'].tail(6).min(),
            'mfi': last.get('MFI_14'),
            'cmf': last.get('CMF_20'),
        }
        signal['_cached'] = False
        _spot_cache["timestamp"] = candle_ts
        _spot_cache["signal"] = signal
        return signal

    except Exception:
        # Re-raise instead of returning None. run_bot.py already wraps this call
        # and keeps the cycle alive, so swallowing here bought nothing and cost
        # the real cause: the caller then subscripted None and reported
        # "'NoneType' object is not subscriptable" instead of the NetworkError.
        logger.exception("[SPOT] analysis failed")
        raise
