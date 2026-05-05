# Changelog

All notable changes to the SpotSignal project.

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
