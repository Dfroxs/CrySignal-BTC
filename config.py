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
    "trailing_atr_factor":   1.0,   # trail distance = ATR × this (tighter than entry SL)
    "max_positions":         3,
}

FUTURES_CONFIG = {
    "enabled":               True,
    "max_leverage":          10,
    "futures_balance":       float(os.getenv("FUTURES_BALANCE", 500)),
    "risk_per_trade":        0.03,
    "max_margin_pct":        0.20,
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
STABLECOIN_CACHE_FILE  = os.path.join(DATA_DIR, "stablecoin_cache.json")
BTC_DOM_CACHE_FILE     = os.path.join(DATA_DIR, "btc_dom_cache.json")
OI_CACHE_FILE          = os.path.join(DATA_DIR, "oi_cache.json")
THRESHOLD_STATE_FILE   = os.path.join(DATA_DIR, "threshold_state.json")


# ---------------------------------------------------------------------------
# Signal thresholds
# ---------------------------------------------------------------------------

# Minimum weighted score required to fire a signal.
SIGNAL_THRESHOLD = float(os.getenv("SIGNAL_THRESHOLD", 5.2))

# Approximate maximum possible score (display only).
#  15.0 → 17.5 (BTC.D + OI) → ~18.0 (liq heatmap).
SIGNAL_MAX_SCORE = 18.0

# Adaptive threshold bounds
THRESHOLD_MIN = 4.0
THRESHOLD_MAX = 8.0

# Rolling window (hours) for adaptive threshold signal counting.
ADAPTIVE_WINDOW_HOURS = 72
# Max signals in the window before raising threshold.
ADAPTIVE_MAX_SIGNALS  = 8


# ---------------------------------------------------------------------------
# Optional integrations — leave blank to disable
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL      = os.getenv("DISCORD_WEBHOOK_URL", "")


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
