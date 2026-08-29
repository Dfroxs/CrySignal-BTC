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

# ── Condition weights, active set, and the thresholds derived from them ─────
#
# CONDITION_MAX is the un-penalised BUY-side ceiling of each scoring block in
# signals/engine.py, keyed by the same names the engine's attribution
# checkpoints emit. It was a comment table; it is data now because the maxima
# and the thresholds have to move together when the condition set changes.
CONDITION_MAX = {
    # condition                spot   futures
    "ema200":                  (1.00, 1.00),
    "rsi":                     (1.50, 1.50),
    "macd":                    (1.50, 1.50),
    "volume":                  (1.00, 1.00),
    "effort_vs_result":        (0.75, 0.75),
    "bollinger":               (1.00, 1.00),
    "htf":                     (2.00, 2.00),
    "obv":                     (0.75, 0.75),
    # funding, L/S, DXY, S&P, stablecoin, BTC.D (+ OI and basis on futures)
    "market_structure":        (3.00, 5.25),
    "stoch_rsi":               (1.25, 1.25),
    "support_resistance":      (0.75, 0.75),
    "vwap":                    (0.75, 0.75),
    "adx":                     (1.25, 1.25),
    "gold_vix":                (0.50, 0.50),
    "oi_price":                (0.00, 0.75),
    "mfi":                     (1.50, 1.50),
    "rsi_divergence":          (2.00, 2.00),
    "extreme_cluster_penalty": (0.00, 0.00),   # only ever subtracts
    "candlestick":             (1.00, 1.00),
    "cmf":                     (1.00, 1.00),
    "taker":                   (0.00, 1.00),
}

# Which conditions are scored. EMPTY — the pruning experiment was run and it
# failed, so nothing is disabled.
#
# The hypothesis was that conditions with no stable candle-level predictive
# power are dead weight, and that dropping the nine worst would leave the same
# result with far fewer parameters. Measured at matched selectivity (the pruned
# threshold calibrated on 2024 by signal count, then applied unchanged to 2025):
#
#   window            full set              pruned set
#   spot 2024         +3.71%  PF 1.58       +2.84%  PF 1.75   (8 vs 6 signals)
#   spot 2025 (OOS)   +0.55%  PF 1.23       −1.33%  PF 0.51   (7 vs 5 signals)
#   futures 2024 H1   +0.64%  PF 1.39       −0.82%  PF 0.00   (5 vs 1 signals)
#
# The full set wins in all three windows. The candle-level measurement says
# those conditions carry no reliable signal ALONE; the trade-level test says
# removing them makes the ensemble worse. Both can be true — a weak, weakly
# correlated component can still improve a joint ranking at the tail where this
# system actually trades, and marginal IC over every candle does not measure
# that. With ~15 trades across both years neither result is conclusive, which is
# itself the reason not to act on either.
#
# The machinery stays: add names here to prune, or run
# `backtest.py --disable a,b` for one run and `--all-conditions` to ignore this
# set entirely. See CHANGELOG 2026-08-29 for the full write-up.
DISABLED_CONDITIONS = frozenset()


def _max_score(mode):
    """Ceiling over the ACTIVE conditions for *mode*."""
    idx = 0 if mode == "spot" else 1
    return round(sum(w[idx] for name, w in CONDITION_MAX.items()
                     if name not in DISABLED_CONDITIONS), 2)


# Thresholds are a FRACTION of the achievable ceiling, not absolute numbers.
# Pruning a condition lowers the ceiling; an absolute bar would then silently
# become a stricter one and the change would be measuring two things at once.
# The fractions reproduce the previous 5.2 / 4.3 / 4.0 / 8.0 / 3.0 / 7.0 exactly
# at the pre-pruning maxima of 26.50 and 22.50.
_THR_FRACTION     = 0.1962      # 5.2 / 26.50
_THR_MIN_FRACTION = 0.1509      # 4.0 / 26.50
_THR_MAX_FRACTION = 0.3019      # 8.0 / 26.50
_SPOT_THR_FRACTION     = 0.1911  # 4.3 / 22.50
_SPOT_THR_MIN_FRACTION = 0.1333  # 3.0 / 22.50
_SPOT_THR_MAX_FRACTION = 0.3111  # 7.0 / 22.50

# Futures signal thresholds
SIGNAL_MAX_SCORE = _max_score("futures")
SIGNAL_THRESHOLD = float(os.getenv("SIGNAL_THRESHOLD", 0)) or round(SIGNAL_MAX_SCORE * _THR_FRACTION, 2)
THRESHOLD_MIN    = round(SIGNAL_MAX_SCORE * _THR_MIN_FRACTION, 2)
THRESHOLD_MAX    = round(SIGNAL_MAX_SCORE * _THR_MAX_FRACTION, 2)

# Spot signal thresholds (4H — no funding/L/S/OI/basis)
SPOT_MAX_SCORE     = _max_score("spot")
SPOT_THRESHOLD     = float(os.getenv("SPOT_THRESHOLD", 0)) or round(SPOT_MAX_SCORE * _SPOT_THR_FRACTION, 2)
SPOT_THRESHOLD_MIN = round(SPOT_MAX_SCORE * _SPOT_THR_MIN_FRACTION, 2)
SPOT_THRESHOLD_MAX = round(SPOT_MAX_SCORE * _SPOT_THR_MAX_FRACTION, 2)

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
