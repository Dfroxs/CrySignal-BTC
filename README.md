# SpotSignal

BTC/USDT trading signal generator — fetches live market data, scrapes news sentiment
from 6 free sources, scores 17 weighted conditions, and outputs BUY / SELL / HOLD
signals with ATR-based stop-loss and take-profit levels.

## Quick Start

```bash
source venv/bin/activate              # Python 3.14
pip install -r requirements.txt       # ccxt, numpy, pandas, requests
cp .env.example .env                  # optional — add Telegram/Discord tokens
python3 run_bot.py                    # one shot
python3 run_bot.py --loop 60          # run every 60 min until Ctrl+C
```

---

## Disclaimer

**This is a hobby project for studying and experimenting with crypto
signal algorithms.** It is not financial advice. Do not trade real
money based on these signals. Have fun, learn something, break stuff —
just don't YOLO your rent money on a bot written on a Sunday afternoon.

Paper trading is simulated. Past backtest results do not guarantee
future performance. Markets are unpredictable. The bot will be wrong
sometimes. That's the fun part — figuring out why.

---

## Scheduling Guide (step by step)

### Option A — built‑in loop mode (simplest)

```bash
source venv/bin/activate
python3 run_bot.py --loop 60
```

The bot will run one cycle every 60 minutes indefinitely.  Press `Ctrl+C` to stop.
Logs are written to both stdout and `spotsignal.log`.

Increase the interval during low‑volatility periods:

```bash
python3 run_bot.py --loop 120   # every 2 hours
```

### Option B — cron job (server / Raspberry Pi)

1. **Create a wrapper script** so cron inherits the correct environment:

```bash
cat > /home/pi/cron_spot.sh << 'EOF'
#!/bin/bash
cd /Users/dfroxs/Playground/Python/SpotSignal
source venv/bin/activate >> cron.log 2>&1
python3 run_bot.py >> cron.log 2>&1
EOF
chmod +x /home/pi/cron_spot.sh
```

2. **Edit crontab** (`crontab -e`) and add one of these schedules:

```cron
# Run every hour at :05 past
5 * * * * /home/pi/cron_spot.sh

# Run every 4 hours
0 */4 * * * /home/pi/cron_spot.sh

# Run at market open (9:30 NY = 13:30 UTC), every weekday
0 13 * * 1-5 /home/pi/cron_spot.sh
```

3. **Verify cron is running** — check `cron.log` after the scheduled time.

> **Important**: If you set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`,
> cron‑triggered runs will send alerts for BUY/SELL signals.  HOLD signals are
> never sent.

### Option C — systemd timer (Linux desktop / VPS)

Persistent, survives reboots, better logging than cron.

1. **Create the service unit** at `/etc/systemd/system/spotsignal.service`:

```ini
[Unit]
Description=SpotSignal BTC Trading Bot

[Service]
Type=oneshot
User=your_user
WorkingDirectory=/opt/SpotSignal
ExecStart=/opt/SpotSignal/venv/bin/python3 /opt/SpotSignal/run_bot.py
StandardOutput=append:/var/log/spotsignal.log
StandardError=append:/var/log/spotsignal.log
```

2. **Create the timer unit** at `/etc/systemd/system/spotsignal.timer`:

```ini
[Unit]
Description=SpotSignal hourly timer

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

3. **Enable and start:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable spotsignal.timer
sudo systemctl start spotsignal.timer
```

4. **Check status:**

```bash
systemctl status spotsignal.timer   # timer running?
journalctl -u spotsignal -f         # live log
```

---

## Notifications

| Channel | Env var | How it works |
|---|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Sends entry/SL/TP/R:R/F&G on BUY or SELL |
| Discord | `DISCORD_WEBHOOK_URL` | Sends compact message on BUY or SELL |

HOLD signals are **never** sent to either channel.  Leave tokens blank to disable.

---

## Individual Scripts

| Script | Purpose |
|---|---|
| `python3 run_bot.py` | Full pipeline: scrape → analyze → paper trade → notify |
| `python3 run_bot.py --loop N` | Loop mode — repeat every N minutes |
| `python3 news_scraper.py` | Phase 1 only — scrape news + macro, export CSVs |
| `python3 core_analysis.py` | Phase 2 only — read stale CSVs, generate signal |
| `python3 backtest.py` | Replay 90 days of 1H data (technical conditions only) |

---

## How the Pipeline Works

### Phase 1 — News & Macro Scraping (`news_scraper.py`)

| Source | Type | Free? |
|---|---|---|
| FinancialJuice | RSS | Yes |
| CoinTelegraph | RSS | Yes |
| Decrypt | RSS | Yes |
| CoinGecko | Trending API | Yes |
| Reddit | r/Bitcoin + r/CryptoCurrency hot posts | Yes |
| ForexFactory | USD macro calendar XML | Yes |

Articles are deduplicated by title, filtered for crypto relevance or geopolitical
impact, sentiment‑scored with word‑boundary keyword matching, then exported to
`data/crypto_news_sentiment.csv` and `data/macro_events.csv`.

Phase 1 failures are non‑fatal — Phase 2 proceeds on stale data.

### Phase 2 — Signal Generation (`core_analysis.py`)

| Step | Details |
|---|---|
| 1. Fetch OHLCV | 1H BTC/USDT from Binance (ccxt), 500 candles |
| 2. Compute indicators | EMA200, RSI, MACD, BB, ATR, OBV, StochRSI, VWAP |
| 3. Parallel fetch (×9) | HTF (4H+1D), funding, L/S, DXY, SP500, stablecoin, BTC.D, OI, F&G |
| 4. Score 17 conditions | Weighted sum → buy / sell scores |
| 5. Macro hold gate | HIGH‑impact USD event ≤2h away → forces HOLD |
| 6. Sentiment overlay | F&G + news blend adjust signal strength |
| 7. Position sizing | ATR‑based SL + TP (2.5× R:R), spot + futures |

### Phase 3 — Paper Trading (`paper_trader.py` / `run_bot.py`)

New BUY/SELL signals open a paper position in SQLite.  Every cycle checks all
open positions against current price — auto‑closes WIN or LOSS when TP or SL
is hit.  P&L accumulated and displayed.

---

## 17 Signal Conditions

Threshold: **5.2** (adaptive: auto‑raises ≥8 signals/72h, lowers at 0 signals).

| # | Condition | Max Weight | Category |
|---|---|---|---|
| 1 | EMA 200 — price above/below | 1.00 | Trend |
| 2 | RSI — oversold / overbought / zones | 1.50 | Momentum |
| 3 | MACD — crossover / position | 1.50 | Momentum |
| 4 | Volume — ≥1.3× avg confirms direction | 1.00 | Volume |
| 5 | Bollinger Bands — at upper / lower band | 1.00 | Volatility |
| 6 | HTF Alignment — 4H + 1D agree | 1.50 | Multi‑TF |
| 7 | RSI Divergence — bullish / bearish | 2.00 | Reversal |
| 8 | OBV — 5‑candle slope (≥0.1% threshold) | 0.75 | Volume |
| 9 | Funding rate + L/S ratio + DXY | 0.50 / 0.75 / 0.50 | Structure |
| 10 | S&P 500 daily trend | 1.00 | Macro |
| 11 | Stablecoin supply (USDT + USDC) | 0.75 | Macro |
| 12 | BTC Dominance (CoinGecko) | 0.75 | Macro |
| 13 | Open Interest trend (Binance Futures) | 0.50 | Structure |
| 14 | Futures Basis — mark vs index spread | 0.50 | Structure |
| 15 | Stochastic RSI — crossovers, reduced when RSI extreme | 0.15–1.00 | Momentum |
| 16 | Support / Resistance — proximity ≤0.3% | 0.75 | Price |
| 17 | VWAP — price relative to volume‑weighted average | 0.75 | Volume |

---

## Configuration

All values in `config.py`, overridable via `.env` or environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `SIGNAL_THRESHOLD` | 5.2 | Minimum score to fire (adaptive override) |
| `SIGNAL_MAX_SCORE` | 18.0 | Display ceiling |
| `ACCOUNT_BALANCE` | 1000 | Spot account balance (USDT) |
| `FUTURES_BALANCE` | 500 | Futures sub‑account balance |
| `LOOP_INTERVAL` | 60 | Loop cadence (minutes) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram target chat |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook URL |

---

## Output Files

| File | Content |
|---|---|
| `data/crypto_news_sentiment.csv` | Scored headlines with sentiment label |
| `data/macro_events.csv` | USD macro calendar events |
| `data/signal_history.csv` | Signal log (CSV fallback) |
| `data/signal_history.db` | Signal log + paper positions (SQLite) |
| `data/stablecoin_cache.json` | Previous stablecoin market cap for trend |
| `data/btc_dom_cache.json` | Previous BTC dominance for trend |
| `data/oi_cache.json` | Previous open interest for trend |
| `data/threshold_state.json` | Adaptive threshold signal counter |

## Backtesting

```bash
python3 backtest.py
```

Replays 90 days of 1H BTC/USDT through `generate_signals()` using technical
conditions only (funding, L/S, DXY, etc. unavailable historically).  Prints
win rate, profit factor, max drawdown, and recent trades.

## Position Sizing

| Parameter | Spot | Futures |
|---|---|---|
| Risk per trade | 2% of balance | 3% of balance |
| Stop loss | 1.5× ATR from entry | same |
| Take profit | 2.5× R:R from SL | same |
| Max leverage | — | 10× |
| Max position | 10% of balance | 20% margin |
| TP cap | Clamped to nearest S/R (min 1.0 R:R) | same |
