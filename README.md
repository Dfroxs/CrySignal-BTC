# SpotSignal

BTC/USDT trading signal generator — dual pipeline (SPOT 4H + FUTURES 1H),
18 weighted conditions, multi-timeframe analysis, macro event gating, paper
trading with trailing stop + partial TP, and compact Telegram notifications.

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

Paper trading is simulated. Past results do not guarantee future performance.

---

## Architecture — 4-Phase Pipeline

```
┌─────────────────────────────────────────────────────────┐
│ PHASE 1 — news_scraper.py                               │
│ 6 RSS sources + CoinGecko + ForexFactory                │
│ → data/crypto_news_sentiment.csv + macro_events.csv     │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 2 — core_analysis.py  (2 independent pipelines)   │
│                                                         │
│  SPOT 4H                    │  FUTURES 1H               │
│  15 conditions              │  18 conditions            │
│  HTF: 1D + 1W EMA200        │  HTF: 4H + 1D EMA200      │
│  Threshold: 4.3 (adaptive)  │  Threshold: 5.2 (adaptive)│
│  Max score: 15.5            │  Max score: 19.25         │
│  No funding/LS/OI/basis     │  Full market structure    │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 3 — paper_trader.py + run_bot.py                  │
│ • Macro gate → force-close all if HIGH impact event <2h │
│ • Position dedup → max 1 BUY + 1 SELL per mode          │
│ • Trailing stop update → advance trail each cycle        │
│ • TP1 (50%) → move trail to breakeven                   │
│ • TP2 (50%) or trail hit → full close                   │
│ • Slippage warning → log if fill >1% past trigger       │
│ • cycle_log → persist every cycle to SQLite             │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 4 — notifier.py                                   │
│ • Compact signal cards per mode (even HOLD)              │
│ • Position close alerts (TP/SL/MACRO)                   │
│ • Open position + P&L summary                           │
│ • Macro risk banner when active                         │
│ → Telegram (HTML) + Discord (markdown)                  │
└─────────────────────────────────────────────────────────┘
```

---

## 18 Signal Conditions

| # | Condition | Max Buy | Max Sell | Notes |
|---|---|---|---|---|
| 1 | EMA 200 trend | 1.00 | 1.00 | Price above/below 200 EMA |
| 2 | RSI zones | 1.50 | 1.50 | <30 OS = +1.5 buy; >70 OB = +1.5 sell; elevated >55 = +0.5 sell |
| 3 | MACD crossover / position | 1.50 | 1.50 | Crossover = +1.5; above/below signal = +0.5 |
| 4a | Volume confirmation | 1.00 | 1.00 | >1.3× avg + direction; <0.7× = weak conviction warning |
| 4b | Volume climax (Wyckoff) | 0.75 | 0.75 | Vol >2× + narrow range (<50% ATR) → accumulation/distribution |
| 5 | Bollinger Bands | 1.25 | 1.25 | At lower/upper band; above/below middle |
| 6 | HTF Alignment (enhanced) | 2.00 | 2.00 | RSI + MACD + volume confirmation per HTF; extreme RSI reversal signals |
| 7 | RSI Divergence (pivot-based) | 2.00 | 2.00 | Swing pivots over 50 candles, >0.2% threshold |
| 8 | OBV 5-candle slope | 0.75 | 0.75 | Only scored if |OBV| / volume ≥ 0.001 |
| 9 | Funding rate | 0.50 | 1.00 | Negative = shorts pay = bullish; VERY HIGH = bearish † |
| 9b | L/S Ratio | 0.75 | 0.75 | <0.8 = shorts crowded (squeeze); >2.0 = longs crowded † |
| 9c | DXY (USD index) | 0.50 | 0.50 | Falling USD = risk-on = buy |
| 10 | S&P 500 trend | 1.00 | 1.00 | >0.5% change = risk-on/off bias |
| 11 | Stablecoin supply | 0.75 | 0.75 | >0.5% change = dry powder entering/leaving |
| 12 | BTC Dominance | 0.75 | 0.75 | >0.5% change = capital rotating in/out |
| 13 | Open Interest | 0.50 | 0.50 | >1% change confirms/contradicts trend † |
| 14 | Futures Basis | 0.50 | 0.50 | Premium >0.1% = long demand; discount <-0.1% = weak † |
| 15 | Stochastic RSI | 1.25 | 1.25 | Crossover in extreme zone; RSI confirmation bonus |
| 16 | Support/Resistance | 0.75 | 0.75 | Bounce/rejection within 0.3% of swing levels |
| 17 | VWAP position | 0.75 | 0.75 | Price above/below rolling VWAP |

† = futures-only conditions (skipped in SPOT pipeline)

**FUTURES max: 19.25** (all 18 conditions)  
**SPOT max: 15.5** (conditions 1-8, 9c-12, 15-17)

---

## HTF Multi-Timeframe Analysis

Each higher timeframe now computes **4 indicators** (not just EMA200):

| Indicator | What it checks |
|---|---|
| Trend | Price vs EMA200 (BULLISH/BEARISH) |
| RSI | Zone: oversold (<30), low (30-45), neutral (45-55), elevated (55-70), overbought (>70) |
| MACD | Direction: ▲ BULLISH / ▼ BEARISH |
| Volume | Trend: ▲ RISING / ▼ FALLING / ─ FLAT (vs 20-period avg) |

**FUTURES:** 4H + 1D analysis  
**SPOT:** 1D + 1W analysis

Scoring is nuanced:
- Aligned + RSI confirms → full weight (+1.5)
- Aligned but RSI warns → reduced (+0.75)
- MACD confirms → bonus +0.25
- Volume rising → bonus +0.25
- Diverging + extreme RSI → reversal signal (+0.75)
- Total cap: 2.0

---

## Macro Event Handling

| Condition | Action |
|---|---|
| HIGH impact USD event <2h away | Signal strength reduced by -2.0; weak signals (≤0) forced to HOLD |
| HIGH impact USD event <2h away | **All open positions force-closed** at market price → outcome `MACRO_CLOSE` |
| Macro risk banner | Sent as separate Telegram message warning of upcoming event |

---

## Position Management

### Opening
- Max **1 BUY + 1 SELL** per mode (dedup check prevents stacking)
- New signal in same direction as existing position → skipped with log message

### Trailing Stop + Partial TP
```
Entry ─┬─ TP1 (50%) → partial exit + trail moves to breakeven
       │
       ├─ TP2 (50%) → full close = WIN
       │
       └─ Trail hit → full close = WIN (if trail ≥ entry) or LOSS
```
- Trailing stop: `price ± ATR × 1.0` (tighter than entry SL at 1.5× ATR)
- P&L blended: `partial_pnl × 0.5 + exit_pnl × 0.5`

### Safety
- **Slippage warning** logged when fill price >1% past trigger (trail or TP)
- **MACRO_CLOSE** outcome tracked separately from WIN/LOSS

---

## Notifications (Telegram)

3-4 compact messages per cycle (instead of 1 long message):

| # | Message | Content |
|---|---|---|
| 1 | SPOT signal card | Verdict, trade setup, technicals, HTF, market, sentiment, top 7 reasons |
| 2 | FUTURES signal card | Same format — includes funding, L/S, OI, basis |
| 3 | Position + P&L | Open positions with entry→TP1 progress, closed P&L, outcome breakdown (W/L/MC) |
| 4 | Macro risk banner | Only when HIGH impact event <2h (sent first if active) |

- **HOLD signals are now sent** (previously silent) — shows gap to fire and why
- **Position close alerts** sent when positions exit (TP1/TP2/Trail/SL/MACRO)
- Each signal card ~700 chars, position card ~300 chars

---

## Configuration

All values in `config.py`, overridable via `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `SPOT_THRESHOLD` | 4.3 | SPOT minimum score (adaptive: 3.0–7.0) |
| `SIGNAL_THRESHOLD` | 5.2 | FUTURES minimum score (adaptive: 4.0–8.0) |
| `ACCOUNT_BALANCE` | 1000 | Spot account balance (USDT) |
| `FUTURES_BALANCE` | 500 | Futures sub-account balance |
| `LOOP_INTERVAL` | 60 | Loop cadence (minutes) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram target chat |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook URL |

**Adaptive threshold:** auto-adjusts based on signal frequency (72h window):
- >8 signals → +0.5 (raises threshold)
- 0 signals → -0.25 (lowers threshold)
- Override: set `SPOT_THRESHOLD` / `SIGNAL_THRESHOLD` in `.env`

---

## Output Files

| File | Content |
|---|---|
| `data/crypto_news_sentiment.csv` | Scored headlines with sentiment label |
| `data/macro_events.csv` | USD macro calendar events |
| `data/signal_history.csv` | Signal log (CSV fallback) |
| `data/signal_history.db` | **Full SQLite database** with 3 tables: |
| | • `cycle_log` — every cycle (including HOLD) with all indicators |
| | • `signals` — triggered BUY/SELL signals with indicator snapshot |
| | • `paper_positions` — all trades with outcomes and P&L |
| `data/stablecoin_cache.json` | Previous stablecoin market cap for trend |
| `data/btc_dom_cache.json` | Previous BTC dominance for trend |
| `data/oi_cache.json` | Previous open interest for trend |
| `data/threshold_state.json` | Adaptive threshold counter (futures) |
| `data/spot_threshold_state.json` | Adaptive threshold counter (spot) |
| `spotsignal.log` | Full log including slippage + macro warnings |

---

## Testing & Analysis

### Run continuously
```bash
source venv/bin/activate
python3 run_bot.py --loop 60
```

### Cycle-level data (for post-hoc analysis)
Every cycle writes 35+ fields to `cycle_log` including:
scores, all technicals, market structure, HTF indicators (JSON), sentiment,
reasons, and open position count.

**Share for analysis:** `data/signal_history.db` + `spotsignal.log`

### Useful SQL queries
```sql
-- Win rate per mode
SELECT mode, outcome, COUNT(*) FROM paper_positions
WHERE outcome IS NOT NULL GROUP BY mode, outcome;

-- Signal frequency
SELECT mode, type, COUNT(*) FROM cycle_log GROUP BY mode, type;

-- Average score by outcome
SELECT p.outcome, AVG(c.strength)
FROM paper_positions p JOIN cycle_log c ON p.signal_id = c.id
WHERE p.outcome IS NOT NULL GROUP BY p.outcome;

-- Threshold drift over time
SELECT timestamp, mode, threshold FROM cycle_log
ORDER BY id DESC LIMIT 50;
```

---

## Individual Scripts

| Script | Purpose |
|---|---|
| `python3 run_bot.py` | Full 4-phase pipeline |
| `python3 run_bot.py --loop N` | Loop mode — repeat every N minutes |
| `python3 news_scraper.py` | Phase 1 only — scrape news + macro, export CSVs |
| `python3 core_analysis.py` | Phase 2 only — read stale CSVs, generate signal |
| `python3 backtest.py` | Replay 90 days of 1H data (technical conditions only) |

---

## Position Sizing

| Parameter | Spot | Futures |
|---|---|---|
| Risk per trade | 2% of balance | 3% of balance |
| Stop loss | 1.5× ATR from entry | same |
| Take profit | 2.5× R:R from SL | same |
| Trailing stop | 1.0× ATR | same |
| Max leverage | — | 10× |
| Max position | 10% of balance | 20% margin |
| Max positions | 2 (1 BUY + 1 SELL) | 2 (1 LONG + 1 SHORT) |
| TP cap | Clamped to nearest S/R (min 1.0 R:R) | same |
