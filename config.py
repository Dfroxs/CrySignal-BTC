import os

RISK_CONFIG = {
    'account_balance':  float(os.getenv('ACCOUNT_BALANCE', 1000)),
    'risk_per_trade':   0.02,
    'max_position_size': 0.10,
    'atr_multiplier':   1.5,
    'take_profit_rr':   2.5,
    'max_positions':    3,
}

FUTURES_CONFIG = {
    'enabled':          True,
    'max_leverage':     10,
    'futures_balance':  float(os.getenv('FUTURES_BALANCE', 500)),
    'risk_per_trade':   0.03,
    'max_margin_pct':   0.20,
}

DATA_DIR              = 'data'
NEWS_CSV              = os.path.join(DATA_DIR, 'crypto_news_sentiment.csv')
MACRO_CSV             = os.path.join(DATA_DIR, 'macro_events.csv')
SIGNAL_HISTORY_CSV    = os.path.join(DATA_DIR, 'signal_history.csv')
STABLECOIN_CACHE_FILE = os.path.join(DATA_DIR, 'stablecoin_cache.json')

# Minimum weighted score (buy or sell side) required to fire a signal.
# Raised from 4.0 → 4.5 to compensate for the additional StochRSI and S/R conditions.
SIGNAL_THRESHOLD = float(os.getenv('SIGNAL_THRESHOLD', 4.5))

# Approximate maximum possible signal score (used in display only).
SIGNAL_MAX_SCORE = 15.0

# Optional integrations — leave blank to disable
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')

LOOP_INTERVAL_MINUTES  = int(os.getenv('LOOP_INTERVAL', 60))
