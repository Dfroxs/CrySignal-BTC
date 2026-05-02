# SpotSignal

BTC/USDT trading signal generator. Fetches market data, news sentiment,
and macro events, then scores 14 technical + macro conditions into BUY / SELL / HOLD
signals with stop-loss and take-profit levels.

## Quick Start

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — edit for Telegram alerts
python3 run_bot.py             # run once
python3 run_bot.py --loop 60   # run every 60 minutes
```

Python 3.14. Dependencies: ccxt, numpy, pandas, requests.

## How It Works

Two-phase pipeline:

### Phase 1 — News & Macro Scraping (`news_scraper.py`)
| Source | Type | Purpose |
|---|---|---|
| FinancialJuice | RSS | Crypto & macro headlines |
| CoinTelegraph | RSS | Crypto news |
| Decrypt | RSS | Crypto news |
| CoinGecko | API | Trending coins |
| ForexFactory | XML | USD macro calendar |

Articles are deduplicated by title, filtered for crypto relevance or geopolitical
impact, then scored for sentiment. Outputs:
- `data/crypto_news_sentiment.csv`
- `data/macro_events.csv`

### Phase 2 — Signal Generation (`core_analysis.py`)

Fetches 1H OHLCV from Binance (ccxt), computes 9 technical indicators, then
pulls 7 parallel data sources via `ThreadPoolExecutor`:

HTF trend (4H + 1D), funding rate, long/short ratio, DXY, S&P 500,
stablecoin supply, Fear & Greed Index.

14 weighted conditions are evaluated, producing buy and sell scores.
A signal fires when the winning side reaches the threshold.

After scoring, the **macro hold gate** checks for HIGH-impact USD events
within 2 hours and forces HOLD if one is imminent. Sentiment from Phase 1
and Fear & Greed then adjust the final signal strength.

### Signal Conditions

| # | Condition | Max Weight | Category |
|---|---|---|---|
| 1 | EMA 200 — price above/below | 1.00 | Trend |
| 2 | RSI — oversold/overbought/zones | 1.50 | Momentum |
| 3 | MACD — crossover / position | 1.50 | Momentum |
| 4 | Volume — ≥1.3× avg confirms move | 1.00 | Volume |
| 5 | Bollinger Bands — at upper/lower band | 1.00 | Volatility |
| 6 | HTF Alignment — 4H + 1D agree | 1.50 | Multi-TF |
| 7 | RSI Divergence — bullish/bearish | 2.00 | Reversal |
| 8 | OBV — 5-candle slope | 0.75 | Volume |
| 9 | Funding rate + L/S ratio + DXY | 0.50 / 0.75 / 0.50 | Structure |
| 10 | S&P 500 trend | 1.00 | Macro |
| 11 | Stablecoin supply | 0.75 | Macro |
| 12 | Stochastic RSI — crossover in oversold/overbought | 1.00 | Momentum |
| 13 | Support/Resistance — bounce/rejection ≤0.3% | 0.75 | Price Action |
| 14 | VWAP — price above/below | 0.75 | Volume |

## Configuration

All values in `config.py`, overridable via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `ACCOUNT_BALANCE` | 1000 | Spot account balance (USDT) |
| `FUTURES_BALANCE` | 500 | Futures sub-account balance |
| `SIGNAL_THRESHOLD` | 4.5 | Minimum weighted score to fire BUY/SELL |
| `SIGNAL_MAX_SCORE` | 15.0 | Maximum possible score (display only) |
| `LOOP_INTERVAL` | 60 | Loop cadence in minutes |
| `TELEGRAM_BOT_TOKEN` | — | Enables Telegram alerts on BUY/SELL |
| `TELEGRAM_CHAT_ID` | — | Target chat for Telegram alerts |

Copy `.env.example` to `.env` and set values there.

## Individual Scripts

```bash
python3 news_scraper.py    # Phase 1 only — scrapes news + macro events
python3 core_analysis.py   # Phase 2 only — reads existing CSVs, generates signal
```

Phase 1 failure is non-fatal in loop mode — Phase 2 proceeds on stale CSV data.

## Telegram Alerts

When `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, BUY/SELL signals
trigger a Telegram message with entry, stop-loss, take-profit, risk/reward
ratio, signal strength, and Fear & Greed reading. HOLD signals are not sent.

## Output Files

| File | Content |
|---|---|
| `data/crypto_news_sentiment.csv` | Scored news headlines with sentiment |
| `data/macro_events.csv` | USD macro calendar events |
| `data/signal_history.csv` | Logged signal history with indicator values |

## Position Sizing

Signals include stop-loss (1.5× ATR below/above entry) and take-profit
(2.5:1 risk/reward). Spot position sizing uses 2% risk per trade with
a 10% max position cap. Futures sizing calculates optimal leverage
up to 10×.
