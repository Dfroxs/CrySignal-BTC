# Changelog

All notable changes to the SpotSignal project.

---

## 2026-05-06 — HTF Analysis Strengthened + Cycle Logging + SPOT HOLD Notifications

### Added

- **Enhanced HTF multi-timeframe analysis** (`core_analysis.py`) — each higher timeframe now computes 4 indicators (not just EMA200): RSI with zone classification, MACD direction, volume trend vs 20-period avg, and price distance from EMA200. `_htf_indicator()` helper extracts all indicators from OHLCV data. HTF scoring is now nuanced: full weight when aligned + RSI confirms, reduced when RSI warns, MACD bonus +0.25, volume bonus +0.25, and diverging HTFs with extreme RSI contribute reversal signals (+0.75). Max HTF score: 2.0 (was 1.5).
- **`cycle_log` table** (`signal_history.py`) — new SQLite table recording every analysis cycle (including HOLD) with 35+ fields: scores, all technical indicators, full market structure snapshot, HTF data as JSON, sentiment, top reasons, and open position count. `log_cycle()` called after each `analyze_*_signal()`. Enables post-hoc analysis of signal quality and threshold tuning.
- **SPOT HOLD notifications** (`notifier.py`) — HOLD signals are now sent to Telegram (previously silent). HOLD cards include gap-to-fire info showing how close the signal is to firing and which direction leads.
- **Position limit clarified** (`config.py`, `run_bot.py`) — log messages now say "max 1 BUY + 1 SELL per mode" instead of generic "max positions reached".

### Changed

- **HTF terminal display** (`core_analysis.py`) — MULTI-TIMEFRAME section now shows RSI value/zone, MACD direction, and volume trend per timeframe alongside trend bias.
- **HTF in notifications** (`notifier.py`) — compact signal card includes RSI + MACD per HTF in the technicals section.
- **Max scores updated** — `SIGNAL_MAX_SCORE`: 18.75 → 19.25, `SPOT_MAX_SCORE`: 15.0 → 15.5 (enhanced HTF adds 0.5).

### Config

- **README rewritten** — full architecture flow diagram, 18 conditions table, HTF analysis detail, macro handling, position management, notification structure, cycle_log documentation with SQL queries, testing guide.

---

## 2026-05-06 — Clean Notification Sections + Position Close Alerts

### Changed

- **Signal card redesigned with section headers** (`notifier.py`) — sections now grouped under emoji headers: 📊 Trade, 📈 Technicals, 🏦 Market, 📰 Sentiment, ✅ Reasons. Each section on its own line group for easy scanning.
- **Position close notifications** (`notifier.py`, `paper_trader.py`, `run_bot.py`) — when positions exit (TP1/TP2/trailing stop/SL/macro force-close), a dedicated Telegram message is sent showing: type, entry→exit, P&L%, and outcome label.
  - `check_and_close_positions()` now returns list of close-event dicts
  - `_format_close_notification()` renders them as compact close cards
  - Phase 3 in `run_bot.py` collects close events and sends notification

---

## 2026-05-06 — Compact Telegram Notifications (Split per Mode)

### Changed

- **Redesigned Telegram format** (`notifier.py`) — single long combined message (~3200 chars) replaced by 2-3 compact separate messages:
  - **Signal card** (~600 chars) per mode (SPOT / FUTURES): verdict, trade setup in code block, technicals one-liner, HTF, market structure, sentiment, top 7 reasons
  - **Position + Performance** (~400 chars): open positions with entry/trail/TP1, closed P&L, outcome breakdown (W/L/MC)
  - **Macro warning** sent as standalone banner when active
- **HOLD signals are silent** — no Telegram message sent when signal is HOLD *(changed 2026-05-06 — HOLD messages now sent)*
- **`_send_telegram_message()`** helper extracted for reusable single-message delivery with label logging
- **`_threshold`** added to signal dict in `core_analysis.py` for compact card display

---

## 2026-05-06 — Terminal Display & Output Improvements

### Changed

- **`print_open_status()`** (`paper_trader.py`) — now shows mode label `[SPOT]` / `[FUTURES]`, outcome breakdown in closed-trades line (`1MC`, `2W · 1L`), and win rate percentage.
- **`print_paper_summary()`** (`paper_trader.py`) — now shows mode label and Outcomes line with WIN/LOSS/MACRO_CLOSE/BREAKEVEN counts.
- **`check_and_close_positions()`** (`paper_trader.py`) — prints a visible terminal banner when macro event force-closes positions (not just log-level).
- **Phase 3 summary** (`run_bot.py`) — all position-management actions are tracked and printed as a summary line at the end of the cycle: `✅ SPOT BUY opened (#N) @ $XX` or `⏭ FUT: Duplicate BUY direction — skipping futures`.

---

## 2026-05-06 — Notifikasi Diperkaya & Position Safety

### Added

- **Open position status in notifications** (`notifier.py`) — Telegram/Discord messages now include a section showing all open positions with entry, trailing stop, and TP1 progress. Also added `_format_position_status()` / `_format_position_status_discord()` helpers.
- **Macro risk warning banner** (`notifier.py`) — when a signal is penalized for an upcoming HIGH impact event, a prominent "MACRO RISK" banner appears at the top of the notification.
- **Outcome breakdown in performance footer** (`notifier.py`) — format `0W · 0L · 1MC` showing WIN / LOSS / MACRO_CLOSE / BREAKEVEN counts plus win rate. `get_outcome_breakdown()` added to `signal_history.py`.
- **Macro-driven position force-close** (`paper_trader.py`) — `check_and_close_positions()` now gates on `check_upcoming_macro_events()`. If a HIGH impact USD event is <2h away, ALL open positions are force-closed at market price with outcome `MACRO_CLOSE`, regardless of mode. Macro risk trumps technical setups.
- **Slippage warnings** (`paper_trader.py`) — `_check_slippage()` logs a warning when fill price is >1% past the trailing stop trigger, making 60-min cycle lag visible in logs.
- **Duplicate same-direction position prevention** (`run_bot.py`, `signal_history.py`) — `has_open_position_same_direction()` checks whether an open position already exists with the same direction before opening a new one. This prevents stacking nearly identical entries when the same signal fires on consecutive cycles.

### Fixed

- **Win rate always None** (`signal_history.py`) — `get_win_rate()` was querying the `signals` table (never populated with outcomes) instead of `paper_positions`. Now queries `paper_positions WHERE outcome IN ('WIN','LOSS')`.

---

## 2026-05-06 — Signal Quality & Performance Metrics Fixes

### Changed

- **RSI Divergence — pivot-based detection** (`core_analysis.py`) — replaced 5-candle lookback with swing pivot detection (50-candle window, 3-candle pivot neighbourhood). The two most recent swing lows/highs are compared; requires >0.2% price difference to filter noise. This is the highest-weight condition (2.0) and was previously prone to false signals from minor price wiggles.
- **Volume climax / Effort-vs-Result** (`core_analysis.py`) — new condition #4b: when volume >2× average AND candle range <50% of ATR, the candle close position determines direction. Close in lower third = accumulation (+0.75 buy), upper third = distribution (+0.75 sell). Classic Wyckoff concept now captured.
- **Macro force HOLD → strength penalty** (`core_analysis.py`) — HIGH impact event within 2h now applies -2.0 strength reduction instead of completely zeroing the signal. Very strong technical setups can still fire with a warning; weak signals that drop to ≤0 are still forced to HOLD.

### Fixed

- **Profit factor now uses actual P&L** (`signal_history.py`) — `get_profit_factor()` was querying theoretical TP/SL distances from the `signals` table instead of realised P&L from `paper_positions`. The displayed profit factor was misleading — a signal with wide TP and tight SL would show high PF even if never reached. Now uses `SUM(pnl_pct)` from actual closed paper positions.
- **Per-mode P&L tracking** (`core_analysis.py`) — `display_analysis()` was splitting total P&L 60/40 (spot/futures) arbitrarily. Now queries `get_closed_pnl(mode='spot')` and `get_closed_pnl(mode='futures')` separately, showing actual per-mode balance changes.
- **Gap to fire shows correct base threshold** (`core_analysis.py`) — NOTE section always referenced `SIGNAL_THRESHOLD` (5.2) even for spot mode (base 4.3). Now uses `SPOT_THRESHOLD` for spot and `SIGNAL_THRESHOLD` for futures.

### Config

- **Max scores updated** — `SIGNAL_MAX_SCORE`: 18.0 → 18.75, `SPOT_MAX_SCORE`: 14.25 → 15.0 (new volume climax condition adds 0.75).

---

## 2026-05-06 — Full-Detail Combined Telegram & Discord Notifications

### Changed

- **`_format_section_telegram()` / `_format_section_discord()`** — Redesigned from compact one-liner to full-detail sections matching terminal output. Both HOLD and non-HOLD signals now show: Price & Trend (price, EMA200, 24h range, S/R), HTF alignment, Technicals (RSI, StochRSI, MACD, VWAP, ATR, OBV, divergence), Market Structure (mode-appropriate fields), Sentiment (F&G, news, top 3 headlines), and Signal Reasons (up to 10). Trade Setup (Entry/SL/TP/RR) only shown for BUY/SELL.
- **Combined message char count** — ~3200 chars for two full sections (SPOT + FUTURES) plus open positions and performance footer, still well under the 4096-char Telegram limit.

---

## 2026-05-06 — Separate Spot & Futures Signal Pipelines

### Added

- **`analyze_spot_signal()`** (`core_analysis.py`) — 4H OHLCV pipeline. Fetches 1D + 1W HTF trend (`get_spot_htf_trend()`), skips futures-only conditions (Funding Rate, L/S Ratio, Open Interest, Futures Basis), uses `VWAP_24` over 6 × 4H candles (= 24H). Adaptive threshold starts at 4.3 (`SPOT_THRESHOLD`), max score 14.25 (`SPOT_MAX_SCORE`).
- **`analyze_futures_signal()`** — renamed from `analyze_btc_signal()` (kept as backward-compat shim). 1H pipeline unchanged: 19 conditions, threshold 5.2, max score 18.0.
- **`get_spot_htf_trend()`** — fetches 1D + 1W EMA200 bias (spot trades on 4H so HTF = daily + weekly).
- **Adaptive threshold per mode** — `get_spot_adaptive_threshold()` / `update_spot_threshold_state()` backed by `spot_threshold_state.json`. Futures uses existing `threshold_state.json`. Both share `_get_adaptive_threshold()` / `_update_threshold_state()` helpers to eliminate duplication.
- **`mode` column in `paper_positions`** — auto-migrated. `open_paper_position(signal, mode='futures')` stores `'spot'` or `'futures'`.
- **Mode-filtered DB queries** — `get_open_positions(mode=None)`, `get_closed_pnl(mode=None)` accept optional mode so spot/futures performance is tracked separately.
- **Combined notifications** — `send_signal_alert(spot_signal, futures_signal)` sends one Telegram/Discord message with both sections. HOLD section shows score only; non-HOLD shows full trade setup. Paper performance footer broken out per mode.
- **Config additions** — `SPOT_THRESHOLD=4.3`, `SPOT_MAX_SCORE=14.25`, `SPOT_THRESHOLD_MIN/MAX`, `SPOT_THRESHOLD_STATE_FILE`, `FUTURES_CONFIG["max_positions"]=2`, `RISK_CONFIG["max_positions"]` reduced 3→2.

### Changed

- **`generate_signals()`** — new params `mode='futures'` and `threshold_override=None`; futures-only conditions wrapped with `if mode == 'futures':`; HTF condition now key-agnostic (works with `{4h,1d}` or `{1d,1w}`).
- **`fetch_ohlcv_df()`** — added `vwap_period=24` param; spot passes `vwap_period=6` for equivalent 24H VWAP on 4H candles.
- **`display_analysis()`** — accepts `timeframe` and `mode`; header shows mode label; MARKET STRUCTURE hides funding/L/S/OI/basis for spot mode; HTF rows rendered dynamically from dict keys.
- **`_signal_box()`** — selects `SPOT_MAX_SCORE` or `SIGNAL_MAX_SCORE` based on `signal['mode']`.
- **`run_bot.py`** — Phase 2 runs spot then futures. Phase 3 manages positions per mode against separate `max_positions` limits. Phase 4 calls combined `send_signal_alert(spot_signal, futures_signal)`.
- **`paper_trader.py`** — `check_and_close_positions`, `print_open_status`, `print_paper_summary` accept `mode=None` and filter accordingly.

---

## 2026-05-06 — Full-Detail Telegram & Discord Notifications

### Changed

- **Telegram & Discord now send complete analysis** — message matches terminal output with all sections: Trade Setup, Price & Trend, Multi-Timeframe, Technicals, Market Structure, Sentiment + headlines, Signal Reasons, and Paper Performance. Previously only basic entry/SL/TP was sent.
- **`analyze_btc_signal()`** now attaches `_htf`, `_market`, `_news_data`, and `_last` (last candle technicals) to the signal dict before returning. These underscore-prefixed keys are ignored by `log_signal()` but consumed by `notifier.py`.
- **Telegram uses HTML parse mode** (tidak lagi Markdown) agar karakter seperti `&`, `<`, `>`, `-`, `.` di judul berita tidak membreak formatting. HTML di-escape otomatis via `_esc()`.
- **Discord uses code block** untuk trade setup agar angka ter-align dengan font monospace.
- **Message size: ~1200 chars** (well within the 4096-char limit).

---

## 2026-05-06 — Critical Deadlock Fix

### Fixed

- **SQLite deadlock causing bot to freeze** (`signal_history.py`) — `_DB_LOCK` was a non-reentrant `threading.Lock()`. On the first DB access in a new process, `_conn()` acquired the lock then called `_init_tables()`, which called `_conn()` again trying to re-acquire the same lock → deadlock. The bot would hang silently after printing "Computing combined sentiment..." (the PERFORMANCE section in `display_analysis` is the first `_conn()` call when `signal_history.csv` doesn't exist). Fixed by changing to `threading.RLock()` (reentrant lock), which allows the same thread to re-enter without deadlocking while still blocking other threads.

---

## 2026-05-06 — Risk & Signal Quality Fixes

### Fixed

- **Paper P&L blended return** (`paper_trader.py`) — combined P&L was averaging two percentages (`(a+b)/2`) instead of weighting each half equally (`a*0.5 + b*0.5`). These are mathematically equivalent when both exits are exactly 50%, but the old formula implied equal sizing which wasn't the intent. Now uses explicit 50/50 blend for clarity and correctness.
- **StochRSI weight inverted** (`core_analysis.py`) — crossover signals were scored at 0.25 when RSI *confirmed* the same zone, suppressing the strongest signals. Fixed: RSI confirmation now adds a bonus (1.25 vs 1.0 for crossover; 0.6 vs 0.5 for zone-only). Strongest signals now score higher.
- **Cache staleness ignored** (`core_analysis.py`) — stablecoin supply, BTC dominance, and open interest caches were compared against any stored value regardless of age. Added `_cache_fresh()` helper (6-hour TTL): stale previous values are now skipped so trend comparison only uses recent data.
- **Max positions not enforced** (`run_bot.py`) — `RISK_CONFIG["max_positions"]` (3) was set in config but never checked before opening a paper position. Now checked against `get_open_positions()` count before every `open_paper_position()` call.

---

## 2026-05-06 — Signal Confidence Label

### Added

- **Signal confidence label** — `get_signal_confidence(strength, threshold)` returns `STRONG` (≥1.5× threshold), `NORMAL` (≥1.2× threshold), or `WEAK` (≥threshold). Stored as `signal['confidence']` in every non-HOLD signal dict.
- **Terminal display** — `_signal_box()` now shows confidence next to strength: green for STRONG, yellow for NORMAL, dim for WEAK.
- **Notifications** — Telegram and Discord alerts include a `Confidence` line showing STRONG/NORMAL/WEAK.

---

## 2026-05-05 — Sumber Berita & Backtest Slippage

### Added

- **BeInCrypto, CoinDesk, Bitcoinist RSS** — tiga sumber berita gratis ditambahkan ke `news_scraper.py` (`fetch_beincrypto`, `fetch_coindesk`, `fetch_bitcoinist`). Setiap sumber diparsing via XML RSS tanpa API key. Total sumber berita naik dari 3 → 6.
- **Slippage model di backtest** — `backtest.py` sekarang mensimulasikan market impact 0.1% per side (`SLIPPAGE_PCT = 0.001`): BUY entry dibayar lebih mahal, TP dan SL diisi lebih buruk. Membuat hasil backtest lebih mendekati live trading.

### Removed

- **Reddit scraper** (`fetch_reddit_sentiment`) — dihapus karena endpoint JSON unofficial (`reddit.com/*.json`) sering return 429 dan tidak reliable. Digantikan oleh 3 sumber RSS di atas.

---

## 2026-05-05 — Notifikasi Telegram & Discord Diperbarui

### Changed

- **Format pesan Telegram & Discord** — notifikasi BUY/SELL sekarang mencantumkan `Trail SL`, `TP1 (50%)`, dan `TP2 (50%)` sesuai mekanisme partial exit yang baru. TP2 hanya muncul jika tersedia di signal dict. Label `Stop Loss` dan `Take Profit` lama diganti dengan terminologi yang mencerminkan trailing stop dan split exit.

---

## 2026-05-05 — Trailing Stop + Partial Take Profit

### Added

- **Trailing stop loss** — `paper_trader.py` now advances the stop loss every cycle as price moves in our favour. For BUY: `trail = price − ATR × trailing_atr_factor`; for SELL: `trail = price + ATR × trailing_atr_factor`. Trail only moves forward (never against the position). Configured via `RISK_CONFIG["trailing_atr_factor"]` (default `1.0`).
- **Partial take profit (TP1 / TP2)** — positions now exit in two halves:
  - **TP1** (first 50%) = original ATR-based TP. When hit, trailing stop moves to breakeven (entry price), locking in no-loss on the remainder.
  - **TP2** (remaining 50%) = 2× the TP1 distance from entry. Closes when TP2 is hit or trailing stop is triggered.
  - Combined P&L = average of both exits.
- **`signal['atr']`** and **`signal['tp2']`** added to signal dict in `generate_signals()`.
- **`update_trailing_stop(pos_id, new_sl)`** and **`partial_close_position(pos_id, pnl_pct, new_sl)`** added to `signal_history.py`.
- **`paper_positions` schema extended** with: `atr`, `trailing_stop`, `tp1`, `tp2`, `partial_closed`, `partial_pnl`. Existing DBs auto-migrated via `_migrate_paper_positions()`.
- Display (`print_open_status`) now shows `Trail` and `TP2` instead of static `SL` and `TP`, plus `[½ taken]` tag after partial exit.

---

## 2026-05-05 — Bug Fixes Round 2

### Fixed

- **CSV fallback dead code** — `log_signal()` had `return cur.lastrowid` placed before the CSV write block, making the backup CSV never update after the SQLite migration. `return` moved to after the CSV write.
- **RSI divergence index mismatch** — `detect_rsi_divergence()` used `.idxmin()` / `.idxmax()` + `.loc[]` to look up RSI at price extremes. On a datetime-indexed DataFrame, label-based lookup can silently return wrong values on duplicate timestamps. Replaced with `tail['close'].values.argmin()` + `.iloc[]` (position-based), which is always correct regardless of index type.
- **Macro event timezone off by 4–5 hours** — ForexFactory exports timestamps in US Eastern Time (ET), but `check_upcoming_macro_events()` was treating them as UTC after the previous fix (`.replace(tzinfo=UTC)`). Events would be shifted 4–5 hours, causing the 2-hour hedge window to fire at wrong times or miss events. Now parsed as ET then converted: `.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)`.
- **Funding rate missing moderate-positive zone** — Funding rates between +0.01% and +0.05% fell through all conditions and scored as NEUTRAL. Added `VERY NEGATIVE` label for rates below -0.05% and confirmed the symmetric coverage now matches positive and negative zones.
- **SQLite connection not thread-safe** — `_conn()` used a bare global `DB` variable without locking. Added `threading.Lock()` (`_DB_LOCK`) and `check_same_thread=False` to prevent "database is locked" errors when scraper and analyzer run concurrently.

---

## 2026-05-05 — Bug Fixes & Signal Integrity

### Fixed

- **Adaptive threshold non-functional** — `generate_signals()` was comparing against the hardcoded `SIGNAL_THRESHOLD` constant instead of calling `get_adaptive_threshold()`. The adaptive mechanism now actually controls signal firing.
- **Duplicate `log_signal()` definition** — `core_analysis.py` had its own CSV-only `log_signal()` that shadowed the SQLite+CSV version in `signal_history.py`. The local definition was removed; `core_analysis` now imports from `signal_history`, ensuring every signal is written to both SQLite and CSV exactly once.
- **Macro event datetime crash** — `check_upcoming_macro_events()` compared a naive `datetime.strptime()` result against `datetime.now(UTC)` (aware), raising `TypeError`. Fixed by appending `.replace(tzinfo=UTC)` to the parsed timestamp.
- **Paper positions not linked to signals** — `paper_positions.signal_id` was never populated (FK defined but unused). `log_signal()` now returns the SQLite `lastrowid`; `analyze_btc_signal()` stores it as `signal['db_id']`; `open_paper_position()` inserts it into the `signal_id` column. Paper trades can now be correlated to their triggering signal.
- **Win rate diluted by BREAKEVEN** — `get_win_rate()` included BREAKEVEN trades in the denominator, making a 10W/10L/20BE record appear as 25% instead of 50%. Denominator is now `WIN + LOSS` only.
- **Profit factor silent on perfect records** — `get_profit_factor()` returned `None` when there were zero losses, making a flawless record indistinguishable from no data. Now returns `float('inf')`; display shows `∞`.
- **OBV denominator unstable near zero** — OBV slope was normalised against `abs(OBV[-5])`, which becomes erratic when OBV crosses zero. Replaced with `volume[-5:].sum()` — a stable, always-positive reference proportional to recent trading activity.
- **Sentiment double-counting** — `analyze_sentiment()` could award two points for the same signal when a short word token (e.g. `bull`) appeared standalone in text that also contained a longer substring match (`bullish`). Word tokens that are prefixes of an already-matched substring are now excluded from the word-match count.

---

## 2026-05-03 — Major Overhaul

### Added

#### New Modules
- **`signal_history.py`** — SQLite-backed signal and paper position storage. Auto-migrates from legacy CSV on first import. Query helpers: `get_recent_signals()`, `get_win_rate()`, `get_profit_factor()`, `get_closed_pnl()`.
- **`paper_trader.py`** — Auto-close open paper positions when TP or SL is hit. Prints open position status and cumulative P&L summary.
- **`backtest.py`** — 90-day replay of 1H BTC/USDT data through `generate_signals()`. Tracks signal outcomes (WIN/LOSS/OPEN), computes win rate, profit factor, max drawdown. Technical conditions only (market structure data unavailable historically).

#### New Signal Conditions
- **#12 BTC Dominance** — CoinGecko `/global` (free). Rising BTC.D = capital rotating into BTC. Weight: 0.75.
- **#13 Open Interest** — Binance Futures `/openInterest` (free). Rising OI = trend confirmation. Weight: 0.50.
- **#14 Futures Basis** — Mark vs index price spread from `premiumIndex`. Premium = long demand, discount = weak demand. Weight: 0.50. (Replaced broken liquidation heatmap — Binance `/allForceOrders` permanently deprecated.)

#### New Data Sources
- **CoinGecko Trending** — `/search/trending` appended to news CSV. BTC in top trending = retail FOMO signal.
- **Reddit Sentiment** — Scrapes `r/Bitcoin` and `r/CryptoCurrency` hot posts, scores with same keyword engine. Free, no key.

#### Features
- **Discord webhook alerts** — `notifier.py` sends to both Telegram and Discord (if `DISCORD_WEBHOOK_URL` is set).
- **Adaptive threshold** — `SIGNAL_THRESHOLD` auto-raises (+0.50) after >8 signals in 72h, lowers (−0.25) after 0 signals. Bounds: 4.0–8.0. Override with env var (any non-zero value disables adaptation).
- **OBV neutral zone** — Only scores OBV when `|slope| / OBV[-5] ≥ 0.001`. Flat OBV no longer contributes noise.
- **TP capped at S/R** — Take-profit clamped to nearest resistance/support when within 85% of ATR-based TP. Minimum 1.0 R:R maintained.
- **StochRSI weight reduction** — When RSI already in extreme zone (<30 or >70), StochRSI crossover weight drops from 1.0 → 0.25, zone weight from 0.5 → 0.15. Eliminates double-counting of correlated oscillators.

### Changed

#### Code Cleanup (all files)
- **Removed duplicate `_make_session()`** — both `news_scraper.py` and `core_analysis.py` now use shared `HTTP_SESSION` from `config.py`.
- **Fixed `datetime.utcnow()` deprecation** — all occurrences replaced with `datetime.now(UTC)`.
- **Fixed redundant `import os`** — `core_analysis.py:960` (line-level duplicate of top-level import).
- **Renumbered conditions** — `generate_signals()` comments now consistent #1–#17 (was #1–#14 with a misnumbered #12 after #14).
- **Added `load_cache()` / `save_cache()`** helpers to `config.py` for atomic JSON cache writes.
- **Added docstrings** to all public functions.
- **Standardized imports** — alphabetized, grouped stdlib → third-party → local.

#### Sentiment Engine
- **Word-boundary matching** — short/ambiguous tokens (`bull`, `ban`, `rise`, `sell`, `buy`) now matched as whole words, avoiding false positives like `bulldozer`, `urban`, `surprise`.
- **Removed `risk` from negatives** — too contextual ("risk-on" is bullish for BTC).
- Extended positive/negative keyword lists with `soar`, `breakout`, `adoption`, `accumulat`, `exploit`, `lawsuit`, `crackdown`, `downturn`, `liquidat`.

#### Configuration
- `SIGNAL_MAX_SCORE`: 15.0 → 18.0
- `SIGNAL_THRESHOLD`: 4.5 → 5.2 (adaptive)
- `ThreadPoolExecutor max_workers`: 5 → 9
- New env var: `DISCORD_WEBHOOK_URL`
- New cache files: `btc_dom_cache.json`, `oi_cache.json`, `threshold_state.json`
- New SQLite DB: `signal_history.db` (auto-created)

#### Pipeline
- **`run_bot.py`** now has a 4-phase cycle: scrape → analyze → paper trade → notify.
- **`analyze_btc_signal()`** calls `update_threshold_state()` after signal generation.
- **`display_analysis()`** shows BTC Dominance, Open Interest, and Futures Basis sections.

### Removed
- **Liquidation heatmap** (`fetch_liquidation_clusters()`) — Binance `/fapi/v1/allForceOrders` permanently deprecated (400 Bad Request). Replaced with futures basis signal that uses existing `premiumIndex` data.

### Fixed
- **`data/` directory not found** — `os.makedirs(DATA_DIR, exist_ok=True)` added to `config.py` on import, so scraping/analysis work even if `data/` was deleted.
