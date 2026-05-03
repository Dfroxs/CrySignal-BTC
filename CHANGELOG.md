# Changelog

All notable changes to the SpotSignal project.

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
