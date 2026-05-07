"""BTC/USDT Trading Signal Generator — backward-compatibility shim.

Re-exports everything from the restructured submodules so existing
import paths (``from core_analysis import ...``) continue to work.

New code should import directly from the target modules:

    from signals.indicators import calculate_rsi
    from signals.market_data import fetch_funding_rate
    from signals.htf import get_htf_trend
    from signals.sentiment import get_combined_sentiment
    from signals.engine import generate_signals
    from signals.sizing import calculate_position_size
    from signals.ohlcv import fetch_ohlcv_df
    from signals.spot import analyze_spot_signal
    from signals.futures import analyze_futures_signal
    from signals.terminal import display_analysis
"""

from signals.market_data import exchange  # noqa: F401

from signals.indicators import (  # noqa: F401
    calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_macd,
    calculate_obv, calculate_rsi, calculate_stoch_rsi, calculate_vwap,
    detect_rsi_divergence, detect_support_resistance,
)

from signals.market_data import (  # noqa: F401
    _cache_fresh, _get_adaptive_threshold, _update_threshold_state,
    fetch_btc_dominance, fetch_dxy_trend, fetch_fear_and_greed,
    fetch_funding_rate, fetch_long_short_ratio, fetch_open_interest,
    fetch_sp500_trend, fetch_stablecoin_supply,
    get_adaptive_threshold, get_signal_confidence, get_spot_adaptive_threshold,
    update_spot_threshold_state, update_threshold_state,
)

from signals.htf import _htf_indicators, get_htf_trend, get_spot_htf_trend  # noqa: F401
from signals.sentiment import check_upcoming_macro_events, get_combined_sentiment  # noqa: F401
from signals.engine import generate_signals, integrate_news_with_signal  # noqa: F401
from signals.sizing import calculate_futures_position, calculate_position_size  # noqa: F401
from signals.ohlcv import fetch_ohlcv_df  # noqa: F401
from signals.spot import analyze_spot_signal  # noqa: F401
from signals.futures import analyze_futures_signal  # noqa: F401
from signals.terminal import _C, display_analysis  # noqa: F401


def analyze_btc_signal(symbol='BTC/USDT', timeframe='1h', include_news=True):
    return analyze_futures_signal(symbol=symbol, include_news=include_news)
