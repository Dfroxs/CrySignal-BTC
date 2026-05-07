"""BTC/USDT Trading Signal Generator — backward-compatibility shim.

This module re-exports everything from the restructured submodules so existing
import paths (``from core_analysis import ...``) continue to work.

New code should import directly from the target modules:

    from indicators import calculate_rsi, detect_support_resistance
    from market_data import fetch_funding_rate, get_adaptive_threshold
    from htf import get_htf_trend, get_spot_htf_trend
    from sentiment import get_combined_sentiment, check_upcoming_macro_events
    from signal_engine import generate_signals, integrate_news_with_signal
    from sizing import calculate_position_size, calculate_futures_position
    from ohlcv import fetch_ohlcv_df
    from spot_analysis import analyze_spot_signal
    from futures_analysis import analyze_futures_signal
    from terminal import display_analysis
"""

# Ensure old `from core_analysis import exchange` still works
from market_data import exchange  # noqa: F401

# Indicators
from indicators import (  # noqa: F401
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_obv,
    calculate_rsi,
    calculate_stoch_rsi,
    calculate_vwap,
    detect_rsi_divergence,
    detect_support_resistance,
)

# Market data
from market_data import (  # noqa: F401
    _cache_fresh,
    _get_adaptive_threshold,
    _update_threshold_state,
    fetch_btc_dominance,
    fetch_dxy_trend,
    fetch_fear_and_greed,
    fetch_funding_rate,
    fetch_long_short_ratio,
    fetch_open_interest,
    fetch_sp500_trend,
    fetch_stablecoin_supply,
    get_adaptive_threshold,
    get_signal_confidence,
    get_spot_adaptive_threshold,
    update_spot_threshold_state,
    update_threshold_state,
)

# HTF
from htf import _htf_indicators, get_htf_trend, get_spot_htf_trend  # noqa: F401

# Sentiment
from sentiment import check_upcoming_macro_events, get_combined_sentiment  # noqa: F401

# Signal engine
from signal_engine import generate_signals, integrate_news_with_signal  # noqa: F401

# Sizing
from sizing import calculate_futures_position, calculate_position_size  # noqa: F401

# OHLCV
from ohlcv import fetch_ohlcv_df  # noqa: F401

# Analysis orchestrators
from spot_analysis import analyze_spot_signal  # noqa: F401
from futures_analysis import analyze_futures_signal  # noqa: F401

# Terminal display
from terminal import _C, display_analysis  # noqa: F401


# Backward-compat shim for old code that calls analyze_btc_signal()
def analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True):
    return analyze_futures_signal(symbol=symbol, include_news=include_news)
