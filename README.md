# SpotSignal

BTC/USDT trading signal generator — dual pipeline (SPOT 4H + FUTURES 1H),
18 conditions, multi-timeframe analysis, macro event gating, paper trading
with trailing stop + partial TP, conviction-based dynamic leverage, and
Telegram notifications.

## Quick Start

```bash
source venv/bin/activate              # Python 3.14
pip install -r requirements.txt       # ccxt, numpy, pandas, requests
cp .env.example .env                  # optional — add Telegram token
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
│ PHASE 2 — signals/ package  (2 independent pipelines)   │
│                                                         │
│  SPOT 4H                    │  FUTURES 1H               │
│  15 conditions              │  19 conditions            │
│  HTF: 1D + 1W EMA200        │  HTF: 4H + 1D EMA200      │
│  Threshold: 4.3 (adaptive)  │  Threshold: 5.2 (adaptive)│
│  Max score: 18.75           │  Max score: 22.25         │
│  No funding/LS/OI/basis     │  Full market structure    │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 3 — trading/ package + run_bot.py                 │
│ • Macro gate → force-close all if HIGH impact event <2h │
│ • SPOT pyramiding → add entries on STRONG signals       │
│ • Position dedup → SPOT BUY-only, FUTURES max 1+1       │
│ • Trailing stop update → advance trail each cycle       │
│ • TP1 (50%) → move trail to breakeven                   │
│ • TP2 (50%) or trail hit → full close                   │
│ • Slippage warning → log if fill >1% past trigger       │
│ • cycle_log → persist every cycle to SQLite             │
│ • Conviction-based dynamic leverage (6-factor model)    │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PHASE 4 — notifier/ package                             │
│ • Combined main signal card (SPOT + FUTURES in one)     │
│ • Position open alerts (dedicated card)                 │
│ • Position close alerts (TP/SL/MACRO/FLIP)              │
│ • Macro risk banner when active                         │
│ → Telegram (HTML)                                       │
└─────────────────────────────────────────────────────────┘
```

---

## 19 Signal Conditions

| # | Condition | Max Buy | Max Sell | Notes |
|---|---|---|---|---|
| 1 | EMA 200 trend | 1.00 | 1.00 | Price above/below 200 EMA + slope (flat = 0.5) |
| 2 | RSI zones | 1.50 | 1.50 | <30 OS = +1.5 buy; >70 OB = +1.5 sell; elevated >55 = +0.5 sell |
| 3 | MACD crossover / position | 1.50 | 1.50 | Crossover = +1.5; above/below signal = +0.5 |
| 4a | Volume confirmation | 1.00 | 1.00 | >1.3× avg + direction; <0.7× = weak conviction warning |
| 4b | Volume climax (Wyckoff) | 0.75 | 0.75 | Vol >1.5× + narrow range (<75% ATR) + directional close |
| 5 | Bollinger Bands | 1.25 | 1.25 | At lower/upper band; squeeze detection (no auto middle score) |
| 6 | HTF Alignment (enhanced) | 2.00 | 2.00 | RSI + MACD + volume per HTF; blocks when both TFs at extreme counter-trend RSI |
| 7 | RSI Divergence (pivot-based) | 2.00 | 2.00 | Swing pivots over 50 candles, ATR-scaled threshold |
| 8 | OBV 5-candle slope | 0.75 | 0.75 | Only scored if \|OBV change\| / volume ≥ 0.002 |
| 9 | Funding rate | 0.50 | 1.00 | Negative = shorts pay = bullish; VERY HIGH = bearish † |
| 9b | L/S Ratio | 0.75 | 0.75 | <0.8 = shorts crowded (squeeze); >2.0 = longs crowded † |
| 9c | DXY (USD index) | 0.50 | 0.50 | Falling USD = risk-on = buy |
| 10 | S&P 500 trend | 1.00 | 0.50 | >0.5% change = risk-on/off bias (spot weight halved) |
| 11 | Stablecoin supply | 0.75 | 0.75 | >0.5% change = dry powder entering/leaving |
| 12 | BTC Dominance | 0.75 | 0.75 | >0.5% change = capital rotating in/out |
| 13 | Open Interest | 0.75 | 0.75 | OI × price direction analysis (4 scenarios) † |
| 14 | Futures Basis | 0.50 | 0.50 | Premium >0.1% = long demand; discount <-0.1% = weak † |
| 15 | Stochastic RSI | 1.25 | 1.25 | Crossover in extreme zone; RSI zone confirmation bonus |
| 16 | Support/Resistance | 0.75 | 0.75 | Bounce/rejection within ATR of nearest swing level |
| 17 | VWAP position | 0.75 | 0.75 | Crossover in last 5 candles (not just position) |
| 18 | ADX trend strength | 1.25 | 1.25 | ADX >25 trending; DI+/DI- crossover + confirmation bonus |
| 19 | Candlestick patterns | 1.00 | 1.00 | Engulfing/Morning/Evening Star (+1.0), Hammer/Shooting Star (+0.75), Harami (+0.5); bearish futures-only |

† = futures-only conditions (skipped in SPOT pipeline)

**FUTURES max: 22.25** (all 19 conditions)  
**SPOT max: 18.75** (conditions 1-8, 9c-12, 15-19; no funding/L/S/OI/basis)

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
- **SPOT: BUY-only** (no short selling on spot market)
- **FUTURES: max 1 LONG + 1 SHORT** (dedup check prevents stacking)
- **SPOT Pyramiding:** when a BUY signal fires with STRONG confidence and a BUY position is already open, opens an additional pyramid entry instead of skipping
  - Max 3 total entries (1 initial + 2 pyramid)
  - Each entry gets progressively tighter SL (×0.8 per level) and smaller size (×0.5 per level)
  - 11 safety gates across first-entry (3 gates) and pyramid (8 gates)
  - TA-driven re-entry: only re-opens if price improved or confidence upgraded
  - Terminal shows `[pyramid #N ×50%]` tag; Telegram uses 🧩 icon

### Trailing Stop + Partial TP
```
Entry ─┬─ TP1 (50%) → partial exit + trail moves to breakeven
       │
       ├─ TP2 (50%) → full close = WIN
       │
       └─ Trail hit → full close = WIN (if trail ≥ entry) or LOSS
```
- Trailing stop: `price ± ATR × 1.0` (spot) / `ATR × 0.9` (futures); tightens to `× 0.8` post-TP1
- P&L blended: `partial_pnl × 0.5 + exit_pnl × 0.5`

### Safety
- **Slippage warning** logged when fill price >1% past trigger (trail or TP)
- **MACRO_CLOSE** outcome tracked separately from WIN/LOSS

---

## Notifications (Telegram)

Per cycle, in order:

| # | Message | Content |
|---|---|---|---|
| 1 | Macro risk banner | Only when HIGH impact event <2h |
| 2 | **Main signal card** | Combined SPOT + FUTURES: verdicts, price & trend, market structure, position sizing, VERDICT with 🔥/❄️, per-mode performance, sentiment, headlines |
| 3 | Position opened | Only when new position opens — entry, SL, TP1, TP2, R/R |
| 4 | Position closed | Only when positions exit — entry→exit, P&L%, outcome label |

- 🔥 = FIRED, ❄️ = HOLD with buy/sell breakdown
- **Per-mode performance** — W/L, win rate, P&L shown separately for SPOT and FUTURES
- **News downgrade** — VERDICT shows "news downgrade → HOLD" when signal drops below threshold post-news
- SPOT HOLD bearish = "BEARISH" + "BUY-only → HOLD" note

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
| `pyramid.enabled` | True | Enable spot pyramiding |
| `pyramid.max_entries` | 3 | Max total entries per direction |
| `pyramid.min_initial_confidence` | NORMAL | Confidence floor for first entry |
| `pyramid.min_confidence` | STRONG | Confidence required to pyramid |
| `pyramid.size_reduction` | 0.5 | Size multiplier per pyramid level |
| `pyramid.tighten_sl_factor` | 0.8 | SL distance multiplier per level |
| `pyramid.max_aggregate_risk_pct` | 5.0 | Max total risk % across all entries |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram target chat |

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

### Spot
- Risk per trade: 2% of balance
- Stop loss: 1.5× ATR from entry
- Take profit: 2.5× R:R from SL
- Max position: 10% of balance
- Max positions: 3 (with pyramiding — 1 initial + 2 pyramid)
- Pyramid entries: tighter SL (×0.8ⁿ⁻¹), smaller size (×0.5ⁿ⁻¹), aggregate risk capped at 5%

### Futures — Conviction-Based Dynamic Leverage
Leverage is not static. A 6-factor model computes a confidence multiplier (0.25–1.5×) and volatility cap (0.33–1.0×) that together determine effective risk and max leverage.

| Factor | Weight | Effect |
|---|---|---|
| Strength vs threshold | ~30% | Score at 2× threshold → +0.30 multiplier |
| HTF alignment | ~25% | Aligned → +0.20, diverging → -0.10 |
| RSI zone confirmation | ~15% | Oversold BUY → +0.15, normal zone → +0.05 |
| Funding rate | ~10% | Negative funding + BUY → +0.10 |
| F&G extreme fear | ~10% | F&G ≤20 + BUY → +0.15 contrarian |
| ATR percentile (volatility) | cap | High vol (≥75%) → leverage cap at 0.50–0.75×, extreme (≥90%) → 0.33× |

```
effective_risk = base_risk × confidence_mult × vol_cap
leverage = clamp(optimal_leverage, 1, max_leverage × vol_cap)
```

| Scenario | Leverage | Risk | Tier |
|---|---|---|---|
| Strong + aligned + low vol | 7–10x | 3–5% | AGGRESSIVE |
| Normal | 3–6x | 2–3% | MODERATE |
| Weak + diverging + high vol | 1–3x | 1–2% | CONSERVATIVE |

- Risk per trade base: 3% of balance
- Max leverage: 10× (before volatility cap)
- Max margin: 20% of balance
- Max positions: 2 (1 LONG + 1 SHORT)
- Trailing stop: 1.0× ATR, partial TP at 50% (TP1), remaining 50% (TP2)
