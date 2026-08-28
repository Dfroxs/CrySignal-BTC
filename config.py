import json
import os
from datetime import UTC, datetime

from dotenv import load_dotenv
from requests import Session

load_dotenv()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------------------------
# Shared HTTP session — retry + exponential backoff on all HTTP calls
# ---------------------------------------------------------------------------

def make_http_session():
    session = Session()
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


HTTP_SESSION = make_http_session()


# ---------------------------------------------------------------------------
# Risk & position sizing
# ---------------------------------------------------------------------------

RISK_CONFIG = {
    "account_balance":       float(os.getenv("ACCOUNT_BALANCE", 1000)),
    "risk_per_trade":        0.02,
    "max_position_size":     0.10,
    "atr_multiplier":        1.5,
    "take_profit_rr":        2.5,
    "trailing_atr_factor":      2.0,  # 1.0→2.0 (audit #8): backtest showed avg-hold=2 candles on spot 4H, paper-cuts dominated
    "trailing_advance_min_ratio": 0.5, # only advance trail if new trail > old + ATR×factor×this
    "trailing_post_tp1_factor":   0.8, # tighten trail 20% after TP1 hit (remaining 50%)
    "max_position_hours":      72,   # force-close futures position if open longer than this
    "max_position_hours_spot": 72,   # spot 4H: 72h = 18 candles; enough room for swing to develop
    "vol_expansion_exit_mult": 2.0,  # close if current ATR > entry ATR × this (root cause was wrong-mode ATR, now per-mode)
    # No max_positions key: concurrent spot positions are capped by
    # pyramid.max_entries below. The old setting was never read by any module.
    "pyramid": {
        "enabled":                 True,       # allow adding to existing position on STRONG signals
        "max_entries":             3,          # 1 initial + 2 pyramid entries max
        "min_initial_confidence":  "NORMAL",   # minimum confidence to open FIRST position
        "min_confidence":          "STRONG",   # signal confidence required to pyramid
        "size_reduction":          0.5,        # each subsequent entry = prev * this multiplier
        "min_size_usdt":           10.0,       # skip pyramid entry if calculated size < this
        "min_entry_distance_atr":  0.5,        # minimum ATR-multiple distance from previous entry
        "max_entry_distance_pct":  6.0,        # max % distance from first entry to allow pyramid
        "tighten_sl_factor":       0.8,        # kept for reference; additive formula used instead
        "tighten_sl_atr_step":     0.25,       # reduce SL by this × ATR per level: 1.5→1.25→1.0 (floor)
        "max_aggregate_risk_pct":  5.0,        # max total risk % of account across all entries
        "psychology_level_step":   1000,       # round numbers at this interval ($80k, $81k, …)
        "psychology_buffer_pct":   0.15,       # % distance considered "near" psychology level
        "sr_entry_risk_atr":       1.0,        # flag if entry within N× ATR of resistance (BUY)
        "fakeout_wick_ratio":      0.60,       # (hi24-close)/(hi24-lo24) > this → rejection wick
    },
}

FUTURES_CONFIG = {
    "enabled":               True,
    "max_leverage":          10,
    "futures_balance":       float(os.getenv("FUTURES_BALANCE", 500)),
    "risk_per_trade":        0.03,
    "max_margin_pct":        0.20,
    # No max_positions key: futures holds at most ONE position at a time —
    # run_bot.py closes an opposite position before opening (close-and-flip)
    # and skips a same-direction one (has_open_position_same_direction).
    # The old setting was never read by any module.
    "entry": {
        "min_confidence":         "NORMAL",   # minimum confidence to open first position
        "reentry_price_check":    True,       # TA-driven re-entry quality gate
        "fakeout_wick_ratio":     0.60,       # reject on >60% upper/lower wick
        "max_aggregate_risk_pct": 8.0,        # max total risk % across all futures positions
    },
    "trailing_atr_factor":   1.5,    # 0.9→1.5 (audit #8): backtest showed avg-hold=4 candles, SELLs that were directionally right got trailed out in 2c
    "funding_exit": {
        "close_long_rate":   0.10,   # close LONG if funding > 0.10% (expensive to hold)
        "close_short_rate": -0.10,   # close SHORT if funding < -0.10% (symmetric with long threshold)
    },
}


# ---------------------------------------------------------------------------
# Paths & data directory
# ---------------------------------------------------------------------------

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

NEWS_CSV               = os.path.join(DATA_DIR, "crypto_news_sentiment.csv")
MACRO_CSV              = os.path.join(DATA_DIR, "macro_events.csv")
SIGNAL_HISTORY_CSV     = os.path.join(DATA_DIR, "signal_history.csv")
SIGNAL_HISTORY_DB      = os.path.join(DATA_DIR, "signal_history.db")
STABLECOIN_CACHE_FILE      = os.path.join(DATA_DIR, "stablecoin_cache.json")
BTC_DOM_CACHE_FILE         = os.path.join(DATA_DIR, "btc_dom_cache.json")
OI_CACHE_FILE              = os.path.join(DATA_DIR, "oi_cache.json")
THRESHOLD_STATE_FILE       = os.path.join(DATA_DIR, "threshold_state.json")
SPOT_THRESHOLD_STATE_FILE  = os.path.join(DATA_DIR, "spot_threshold_state.json")


# ---------------------------------------------------------------------------
# Signal thresholds
# ---------------------------------------------------------------------------

# SIGNAL_MAX_SCORE / SPOT_MAX_SCORE are display denominators only ("score X / max"
# in the terminal and Telegram cards) — nothing gates on them. Each is the
# un-penalised BUY-side ceiling: the sum of the best case of every independent
# scoring block in signals/engine.py. A realised score can sit below it after the
# correlated-extreme penalty (−2.25 max) or the HTF conflict penalty (−1.0).
# Keep this table in sync when adding or reweighting a condition:
#
#   block                        spot   futures
#   EMA200 trend + slope         1.00      1.00
#   RSI                          1.50      1.50
#   MACD crossover (ADX ≥ 20)    1.50      1.50
#   Volume confirmation          1.00      1.00
#   Effort vs Result (Wyckoff)   0.75      0.75
#   Bollinger Bands              1.00      1.00
#   HTF alignment (capped)       2.00      2.00
#   OBV slope                    0.75      0.75
#   Funding bias                 0.25      0.50
#   Long/Short ratio             0.25      0.75
#   DXY                          0.50      0.50
#   S&P 500                      0.50      1.00
#   Stablecoin supply            0.75      0.75
#   BTC dominance                0.75      0.75
#   Open interest                   —      0.50
#   Futures basis                   —      0.50
#   OI × price direction            —      0.75
#   Stochastic RSI               1.25      1.25
#   Support / resistance         0.75      0.75
#   VWAP crossover               0.75      0.75
#   ADX + DI crossover           1.25      1.25
#   Gold + VIX                   0.50      0.50
#   RSI divergence               2.00      2.00
#   Candlestick pattern          1.00      1.00
#   MFI                          1.50      1.50
#   CMF                          1.00      1.00
#   Taker buy/sell ratio            —      1.00
#   ──────────────────────────────────────────
#   total                       22.50     26.50

# Futures signal thresholds
SIGNAL_THRESHOLD = float(os.getenv("SIGNAL_THRESHOLD", 5.2))
SIGNAL_MAX_SCORE = 26.50
THRESHOLD_MIN    = 4.0
THRESHOLD_MAX    = 8.0

# Spot signal thresholds (4H, 15 conditions — no funding/L/S/OI/basis)
SPOT_THRESHOLD    = float(os.getenv("SPOT_THRESHOLD", 4.3))
SPOT_MAX_SCORE    = 22.50
SPOT_THRESHOLD_MIN = 3.0
SPOT_THRESHOLD_MAX = 7.0

# Rolling window (hours) for adaptive threshold signal counting.
ADAPTIVE_WINDOW_HOURS = 72
# Max signals in the window before raising threshold.
ADAPTIVE_MAX_SIGNALS  = 8


# ---------------------------------------------------------------------------
# Optional integrations — leave blank to disable
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")

# Dynamic leverage config — conviction-based multipliers
LEVERAGE_CONFIG = {
    "base_max_leverage": 10,          # absolute cap
    "atr_lookback": 100,             # candles for percentile calc
    "fractional_kelly": 0.25,        # 1/4 Kelly as base risk fraction
    "confidence_mult_min": 0.25,     # floor
    "confidence_mult_max": 1.5,      # ceiling
    "maintenance_margin_rate": 0.005, # Binance: 0.5% at ≤10x, 1.0% at 20x+
}

EXECUTION_CONFIG = {
    "spot_fee_pct":       0.10,   # Binance spot taker fee (%)
    "futures_fee_pct":    0.04,   # Binance futures taker fee (%)
    "slippage_pct":       0.05,   # market order slippage (%)
}

RISK_LIMITS = {
    "max_drawdown_pct":   15.0,   # stop new entries if drawdown > this
    "min_equity_pct":     50.0,   # close all + emergency stop if equity < this
    "daily_loss_limit":   5.0,    # stop for the day if daily loss > this
}


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def load_cache(path):
    """Return parsed JSON dict from *path*, or {} if missing/corrupt."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(path, data):
    """Atomically write *data* dict as JSON to *path*."""
    data["ts"] = datetime.now(UTC).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


LOOP_INTERVAL_MINUTES = int(os.getenv("LOOP_INTERVAL", 60))
