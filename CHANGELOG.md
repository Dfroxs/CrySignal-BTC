# Changelog

All notable changes to the SpotSignal project.

---

## 2026-05-10 — feat: Condition #19 — Candlestick Pattern Recognition

### Added

- **Candlestick pattern recognition — Condition #19** (`signals/indicators.py`, `signals/engine.py`): New `detect_candlestick_pattern(df)` detects 4 bullish and 4 bearish reversal patterns from the last 3 OHLCV candles. Only the highest-weight pattern per direction is counted (no stacking). Bearish patterns are only scored in futures mode (spot is BUY-only).
  - Bullish: ENGULFING (+1.0), MORNING_STAR (+1.0), HAMMER (+0.75), HARAMI (+0.5)
  - Bearish: ENGULFING (+1.0), EVENING_STAR (+1.0), SHOOTING_STAR (+0.75), HARAMI (+0.5)
- **Max scores updated** (`config.py`): `SPOT_MAX_SCORE` 17.75 → 18.75, `SIGNAL_MAX_SCORE` 21.25 → 22.25

---

## 2026-05-10 — Low Priority Fixes: Backtest Parity, Adaptive Threshold, Wyckoff, S/R Recency

### Changed

- **Backtest trail now matches live paper.py behavior** (`backtest.py`): `_simulate_forward()` reads `trailing_post_tp1_factor` (0.8) and `trailing_advance_min_ratio` (0.5) from `RISK_CONFIG`, applying the same minimum-advance gate and post-TP1 tightening as `paper.py`. Previously backtest trail was always identical to paper.py from before the critical fixes — results were now diverging.
- **Backtest futures funding exit proxy** (`backtest.py`): Simulates a FUNDING_EXIT when unrealized gain exceeds 12% on a futures position (proxy for crowded funding regime). Historical funding data is unavailable in backtest, but extreme sustained moves strongly correlate with positive funding. Applies to both BUY and SELL directions before partial TP.
- **Adaptive threshold: 24h fast window added** (`signals/market_data.py`): `_get_adaptive_threshold()` now checks a 24h window first: if ≥4 signals fired AND win rate < 30%, threshold raises +1.0 immediately (vs the slow 72h raise of +0.5/0.75). Addresses lag where a losing streak on day 1-2 wasn't reflected until day 4. 72h standard window unchanged.
- **Wyckoff Effort vs Result thresholds relaxed** (`signals/engine.py`): Volume climax threshold lowered from `2.0×` → `1.5×` avg and range threshold from `< 0.5×` → `< 0.75×` ATR, making the pattern fire more often. Added directional confirmation: accumulation requires green close (close ≥ open), distribution requires red close. Close position thresholds widened to 0.40/0.60 from 0.35/0.65.
- **S/R detection returns nearest level (not farthest) + recency preference** (`signals/indicators.py`): Previous code sorted resistance descending and returned the highest (furthest) level — now returns the lowest resistance above close (nearest). Among pivots within one ATR band of the nearest level, the most recent pivot is preferred. Same logic applied to support. This corrects both the direction bug and stale-pivot preference.

---

## 2026-05-10 — Medium Quality Fixes: Daily Limit, HTF Volume, TP2 Resistance, Sentiment Freshness, Divergence ATR

### Fixed

- **Daily loss limit circuit breaker now enforced** (`trading/history.py`, `run_bot.py`): Added `get_daily_pnl()` to history.py (sums closed P&L since UTC midnight). `run_bot.py` now checks `daily_pnl < -daily_loss_limit` (default 5%) alongside the existing drawdown check. Both conditions block new entries and send a Telegram alert. Previously `RISK_LIMITS["daily_loss_limit"]` was defined but never used.
- **HTF volume trend uses EWM instead of SMA** (`signals/htf.py`): `_htf_indicators()` now computes `vol_ema5` and `vol_ema20` via `ewm(span=…, adjust=False)`. The previous `rolling(20).mean()` and `tail(5).mean()` gave equal weight to candles 20 weeks ago (on 1W) and last week — EWM weights recent volume higher, making trend detection more responsive.
- **TP2 capped below nearest resistance (BUY) / above support (SELL)** (`signals/engine.py`): If `support_resistance` contains a level between entry and the raw TP2, TP2 is set to `resistance × 0.995` (or `support × 1.005` for shorts). Prevents setting aggressive TP2 targets past a strong structure level that typically absorbs price.
- **News sentiment ignores articles older than 24h** (`signals/sentiment.py`): CSV is now filtered to rows with `timestamp >= now - 24h` before the `head(7)` selection. If scraper stalled, stale headlines no longer bias the combined sentiment score. Rows are sorted newest-first so the 7 freshest articles are always used.
- **RSI divergence threshold scales with ATR** (`signals/indicators.py`): Fixed `threshold = 0.002` replaced with `max(0.002, ATR / price)`. At BTC $100k with ATR $200 (0.2%), threshold stays at 0.2%. At high volatility with ATR $500 (0.5%), threshold rises to 0.5% — preventing noise pivots from triggering false divergence signals.

---

## 2026-05-10 — Critical Quality Fixes: Trail Noise, HTF False Positives, Confidence Staleness, Regime Filter

### Fixed

- **Trailing stop no longer ratchets on micro-moves** (`trading/paper.py`, `config.py`): Trail only advances when the new level is at least `ATR × trail_factor × 0.5` above the current trail. Previously, any single-candle close slightly above trail would update it, causing gradual ratcheting in sideways markets and premature stop-outs on normal pullbacks. Added `trailing_advance_min_ratio: 0.5` to `RISK_CONFIG`.
- **Trailing stop tightens 20% after TP1 hit** (`trading/paper.py`, `config.py`): After the first 50% partial close, the remaining position uses `trail_factor × 0.8` instead of the full factor. At half position size the risk profile is lower, so a tighter trail protects the accumulated gain more aggressively. Added `trailing_post_tp1_factor: 0.8` to `RISK_CONFIG`.
- **HTF aligned flag no longer false-positive on momentum exhaustion** (`signals/htf.py`): `aligned=True` now requires that the two HTF timeframes are NOT both in an extreme counter-trend RSI zone. Example: 4H BULLISH + 1D BULLISH both overbought → `aligned=False` (impending reversal, not a safe buy setup). Both `get_htf_trend()` (futures) and `get_spot_htf_trend()` (spot) updated via shared `_htf_aligned()` helper.
- **Confidence recalculated after news integration** (`signals/engine.py`): `integrate_news_with_signal()` now calls `get_signal_confidence()` at the end to reflect post-news strength. Previously a signal that dropped from strength 6.5 → 4.2 after news was still labeled "STRONG", allowing pyramid entries to open on stale confidence.
- **Regime filter only blocks TRENDING + BEARISH entries** (`run_bot.py`): `_is_bearish_regime()` now requires `regime == "TRENDING"` in addition to `trend_dir == "BEARISH"`. In ranging/transition markets (ADX < 20-25), DI- > DI+ is normal oscillation — blocking spot BUY in these conditions was incorrectly rejecting valid pullback entries.

---

## 2026-05-10 — Strategy Tuning: 10 Parameter Fixes (Vol Exit, Trail, OBV, S&P, VWAP, Funding, Time Exit, EMA Slope, BB Squeeze, Pyramid SL)

### Changed

- **EMA 200 condition now includes slope** (`signals/engine.py`): Condition #1 differentiates between price above a rising EMA (full 1.0 pt) vs price above a flat/falling EMA (0.5 pt). Previously, a bullish position in a months-long uptrend always scored 1.0 regardless of EMA momentum. Uses a 5-candle slope to avoid single-candle noise.
- **Bollinger Band middle zone replaced with squeeze detection** (`signals/engine.py`): The unconditional ±0.25 "price above/below BB middle" score is replaced with a volatility compression check. Score only awarded when the current BB width is in the bottom 30th percentile of the past 20+ candles (squeeze), combined with price direction vs middle. This was previously firing on almost every non-extreme candle.
- **Pyramid SL tightening: multiplicative → additive ATR-based** (`run_bot.py`, `config.py`): SL per pyramid level now uses `entry - atr × max(1.0, 1.5 - 0.25 × (n-1))` instead of `sl_dist × 0.8^(n-1)`. Results: Entry #2 = 1.25× ATR (was 0.8×), Entry #3 = 1.0× ATR (was 0.64×). The multiplicative formula compounded to below-wick levels at entry #3; the additive formula has a hard 1.0× ATR floor. Added `tighten_sl_atr_step: 0.25` to pyramid config.

---

## 2026-05-10 — Strategy Tuning: 7 Parameter Fixes (Vol Exit, Futures Trail, OBV, S&P, VWAP, Funding, Time Exit)

### Changed

- **S&P500 weight halved for spot mode** (`signals/engine.py`): S&P500 bias now scores 0.5 (spot) vs 1.0 (futures). BTC-SPX correlation weakens during crypto-driven cycles; 1.0 was equivalent to a MACD crossover — too high for an external macro factor. `SPOT_MAX_SCORE` updated 18.25 → 17.75.
- **VWAP requires recent crossover** (`signals/engine.py`): Condition #17 now only scores when price crossed the VWAP in the last 5 candles (was below/above within lookback window). Pure "price above VWAP" in a sustained trend no longer awards 0.75 automatically — that was effectively a free score in any uptrend.
- **Funding exit short threshold symmetric** (`config.py`): `close_short_rate` raised from `-0.05%` → `-0.08%`. The previous asymmetry (long closed at >0.10%, short closed at <-0.05%) was treating shorts as far more sensitive than longs. `-0.08%` is more proportional.
- **Spot time exit reduced to 48h** (`config.py`, `trading/paper.py`, `backtest.py`): Added `max_position_hours_spot: 48`. BTC 4H signals typically materialize within 12-15 candles (48-60h); holding to 72h locks capital in declining-quality setups. Futures unchanged at 72h. Both paper.py and backtest.py now read mode-aware values.

---

## 2026-05-10 — Strategy Tuning: 3 Parameter Fixes (Vol Exit, Futures Trail, OBV Filter)

### Changed

- **Vol expansion exit threshold tightened** (`config.py`): `vol_expansion_exit_mult` reduced from `2.0` → `1.5`. BTC 4H ATR can spike 1.8-2.0× in a single candle during news events, meaning the 2.0× exit was triggering *after* damage already occurred. 1.5× catches exhaustion earlier while still filtering noise.
- **Futures trailing stop loosened** (`config.py`): `FUTURES_CONFIG["trailing_atr_factor"]` raised from `0.7` → `0.9`. At 0.7× ATR on 1H candles, normal wicks frequently triggered premature trail exits — the tighter trail was intended to reflect leverage amplification, but 0.7× is below typical wick size on BTC 1H. Spot trail unchanged at 1.0×.
- **OBV activation threshold tightened** (`signals/engine.py`): OBV signal threshold raised from `obv_rel >= 0.001` → `>= 0.002`. The 0.001 threshold fired on nearly every candle with any volume, making the 0.75-point OBV condition a near-automatic score contribution. 0.002 requires meaningful net OBV flow relative to 5-candle volume.

---

## 2026-05-10 — Pyramid Strategy Review: 2 Bug Fixes (Cache Mutation, P&L Weighting)

### Fixed

- **Spot signal cache no longer mutated by run_bot** (`signals/spot.py`): `analyze_spot_signal()` was returning the cached dict reference directly. `run_bot.py` mutates `spot_signal["type"]`, `stop_loss`, `take_profit`, and `tp2` at multiple points (HOLD forcing, SL tightening for pyramid entries). Subsequent bot cycles within the same 4H candle received a corrupted cached signal — e.g. a forced HOLD would suppress valid signals for the rest of the candle, and pyramid SL tightening would compound on itself each cycle. Fixed by returning `dict(_spot_cache["signal"])` (shallow copy) on cache hit.
- **Pyramid P&L now weighted by size_factor** (`trading/history.py`): `close_paper_position()` was storing raw `pnl_pct` for all positions regardless of pyramid size. Entry #3 (25% of base size) was reporting the same percentage contribution as Entry #1 (100%), inflating aggregate P&L stats and win-rate calculations. Fixed in `close_paper_position()` by multiplying `pnl_pct` by the position's stored `size_factor` (1.0 for initial entries, 0.5/0.25 for pyramid entries) before storing.

---

## 2026-05-10 — Full Codebase Review: 3 Bug Fixes (Paper Trail Fee, Sizing Threshold, Docstring)

### Fixed

- **`paper.py` trailing stop exits now apply exit fees** (`trading/paper.py`): BUY and SELL trailing stop exits were computing P&L directly (`(trail - entry) / entry`) without fees. All other exit types (MACRO_CLOSE, TIME_EXIT, VOL_EXIT, FUNDING_EXIT) use `_calc_pnl()` which subtracts `(fee + slippage)`. Both trail branches now call `_calc_pnl(pos, trail)` for consistency — trail exits were overstating P&L by ~0.15% (futures) or ~0.30% (spot) per trade.
- **Spot position sizing ATR percentile thresholds use `>=` consistently** (`signals/sizing.py`): spot used strict `>` while futures used `>=`, causing a one-position edge-case discrepancy at exactly `atr_pct = 0.90` and `0.75`. Both now use `>=`.
- **`spot.py` docstring updated to match actual behavior** (`signals/spot.py`): docstring claimed "15 conditions (no funding/L/S/OI/basis)" but the pipeline fetches funding rate and L/S ratio, which engine.py uses at ½-weight (0.25 each) for spot. Docstring now accurately describes behavior; OI and basis remain excluded.

---

## 2026-05-10 — Technical Indicator Review: 4 Correctness Fixes (ADX, Backtest HTF)

### Fixed

- **ADX minus DM formula corrected** (`signals/indicators.py`): was using `low.diff().abs()` (always positive) causing false -DM readings on gap-up days. Fixed to `-low.diff()` so -DM only fires when price actually moves down, matching Wilder's canonical definition.
- **Backtest HTF MACD now uses actual MACD** (`backtest.py`): was comparing `ohlc[-1] > ohlc[-2]` (price direction) instead of EMA12−EMA26 vs signal line EMA9. Now calls `calculate_macd()` from `signals/indicators.py` — same function used by the live engine.
- **Backtest HTF RSI now uses 5-zone classification** (`backtest.py`): was using 3 zones (oversold/neutral/overbought) while live `htf.py` uses 5 (oversold/low/neutral/elevated/overbought). Unified to match live behavior.
- **Backtest HTF RSI now uses Wilder's algorithm** (`backtest.py`): was using EWM approximation directly on gain/loss. Now calls `calculate_rsi()` from `signals/indicators.py` — same Wilder's iterative smoothing used by the live engine.

---

## 2026-05-10 — Futures Strategy Review: 3 Bug Fixes (Backtest Double-Bump, OI Asymmetry)

### Fixed

- **Backtest no longer double-applies regime + session threshold bumps** (`backtest.py`): `effective_threshold` was computed as `base + regime_bump + session_bump` before being passed to `generate_signals()`, which then added them again internally (regime from same window = identical value; session from `datetime.now()` = wrong historical time). Backtest now passes just the base adaptive threshold; the engine applies both adjustments exactly once.
- **OI×Price bear case now symmetric with bull case** (`signals/engine.py`): `OI↑+Price↓` (new shorts opening as price falls = confirmed distribution) was scored `+0.5` sell while the mirror condition `OI↑+Price↑` scored `+0.75` buy. Updated to `+0.75` — equal information strength in opposite directions.
- **`SIGNAL_MAX_SCORE` comment updated** (`config.py`): replaced stale calculation comment with accurate description (practical max ~21.25 after diminishing-returns penalty; theoretical ceiling ~22).

---

## 2026-05-10 — Spot Strategy Review: 5 Bug Fixes (Scoring, Backtest Fees, Gates)

### Fixed

- **RSI divergence no longer double-inflates scores** (`signals/engine.py`): when bullish divergence fires against an overbought RSI, the OB sell score is now cancelled (−1.5) before adding to buy; previously only the buy side increased, leaving sell inflated. Same fix applied for bearish divergence + oversold RSI.
- **RSI divergence now immune to diminishing-returns penalty** (`signals/engine.py`): divergence scoring block moved to after the correlated-extremes penalty block. Divergence measures price-RSI momentum — structurally independent from RSI/BB/StochRSI price extremes — so it should not be discounted when 3 extremes cluster. Both old blocks (early scoring + override) replaced with a single consolidated post-penalty block.
- **Backtest trailing stop exits now deduct exit fees** (`backtest.py`): trailing stop P&L was computed inline without any exit cost. Now applies `(fee_pct + slippage)` to the trail price before computing P&L, consistent with how TP targets are fee-adjusted.
- **Backtest TIME_EXIT / VOL_EXIT now use mode-aware taker fee** (`backtest.py`): `_calc_backtest_pnl` was applying slippage-only on exit (comment read "fee already in entry"). Updated to apply the correct taker fee per mode (0.10% spot, 0.04% futures) plus slippage on exit, matching the live `trading/paper.py` cost model.
- **Backtest spot entry gates now include psychology SL check** (`backtest.py`): added gate matching live `run_bot.py` behavior — skips entry if SL is within 0.15% below a $1,000 round number (stop-hunt zone).
- **`SPOT_MAX_SCORE` comment updated** (`config.py`): updated value to 18.25 and corrected comment; previous value (17.25) was an underestimate from an older condition set.

---

## 2026-05-09 — Strategy Overhaul: Signal Quality, Risk Gates, Exit Conditions, Formula Fixes

### Added

- **5 signal quality improvements** (`signals/engine.py`, `signals/spot.py`, `signals/market_data.py`):
  - **Diminishing returns** on correlated OS/OB conditions (RSI+BB+StochRSI): 1st full weight, 2nd −0.75, 3rd −1.5
  - **RSI divergence priority** — suppresses contradictory RSI zone score (divergence is stronger)
  - **Spot vs Futures directional conflict** — when spot=BUY and futures=SELL, both downgraded to HOLD
  - **4H candle caching** (`signals/spot.py`) — returns cached result within same candle, saves 75% API calls
  - **Quality-aware adaptive threshold** (`signals/market_data.py`) — win rate <35% → +0.75 raise; ≥60% → −0.5 drop
- **6 medium-impact strategy improvements**:
  - **Time-based exit** (`trading/paper.py`) — force-close positions older than `max_position_hours` (72h)
  - **Vol-expansion exit** — close if current ATR > entry ATR × `vol_expansion_exit_mult` (2.0×)
  - **First-entry TA gates** (`run_bot.py`) — added psychology SL + S/R proximity to first entry (was 3, now 5 gates)
  - **S/R proximity ATR-scaled** (`signals/engine.py`) — 0.2× ATR replaces hardcoded 0.3%
  - **F&G contradiction** — BUY into GREED (≥70) or SELL into FEAR (≤30) penalized −0.5
  - **HTF scoring** — aligned ≥1.0, diverging ≤0.5 (was both 0.75)
  - **Funding vs L/S tie-breaking** — net weight to dominant side on conflict
- **Futures entry safety gates** (`run_bot.py`) — confidence floor (NORMAL), fakeout rejection, re-entry quality, aggregate risk cap (8%). Flip path also gated.
- **Funding-based exit** (`trading/paper.py`) — close LONG if funding >0.10%, close SHORT if ←0.05%
- **ADX/DI trend strength** (`signals/indicators.py`, `signals/engine.py`) — condition #18: ADX >25 trending +0.5, DI+/DI- crossover +0.75. Replaces binary EMA200 with proper trend quantification.
- **Regime classifier** (`signals/indicators.py`) — TRENDING (ADX>25, threshold −0.25), RANGING (ADX<20, +0.5), VOLATILE (ATR>90th, +0.25, size×0.75), TRANSITION (normal)
- **OI×Price directional** (`signals/engine.py`) — OI↑+Price↑ = healthy uptrend (+0.75), OI↑+Price↓ = distribution (+0.5), OI↓+Price↑ = short squeeze (+0.25), OI↓+Price↓ = liquidation cascade (+0.5)
- **Session-based threshold** (`signals/engine.py`) — Asia (0-7 UTC) +0.5, US (13-22 UTC) −0.25
- **4 futures strategy improvements**:
  - **F&G symmetry** (`signals/sizing.py`) — SELL at extreme greed (≥80) gets +0.15 (was BUY-only)
  - **Flip profitability gate** (`run_bot.py`) — block flip if total loss > expected reward
  - **Proper liquidation formula** — `entry × (1 - (1 - mm_rate) / leverage)` replaces 0.95 fudge
  - **Futures trailing stop** — 0.7× ATR (was 1.0× shared with spot)
- **News improvements** (`news_scraper.py`):
  - **Freshness filter** — drop headlines older than 24h via RSS pubDate parsing (~40% reduction)
  - **Sentiment normalization** — `score / √word_count` bounds raw keyword count by headline length
- **Spot improvements**:
  - **Volatility-adjusted sizing** (`signals/sizing.py`) — max_position_size scaled by ATR percentile (matching futures)
  - **ATR percentile in spot signal** (`signals/spot.py`) — was futures-only
  - **Futures sentiment into spot** (`signals/spot.py`, `signals/engine.py`) — funding + L/S as lightweight sentiment (0.25 weight each)
- **Max scores updated**: Futures 19.25→21.25, Spot 15.5→17.25

### Fixed

- **Wilder's RSI formula** (`signals/indicators.py`) — replaced Cutler's SMA (`rolling(window).mean()`) with proper Wilder's EMA (`avg = (prev × 13 + current) / 14`). Affects RSI, StochRSI, divergence detection.
- **Wilder's ATR formula** (`signals/indicators.py`) — replaced SMA with `ewm(alpha=1/period)`. Now consistent with ADX smoothing.
- **3 code bugs**: `_CURRENT_ATR` NameError (crash on vol-exit), `_last_atr` missing `global` (mid-cycle vol-exit disabled), `threshold=None` TypeError
- **Undefined `last_entry`**, KeyError risk in gate messages, redundant re-imports

### Removed

- **Post-pyramid 12h cooldown** — redundant with TA-driven `_check_reentry_quality()` which already blocks re-entry when price is worse with no confidence upgrade

---

## 2026-05-09 — Dynamic Leverage + Bug Fixes + Polish

### Added

- **Spot pyramiding** (`run_bot.py`, `config.py`, `signals/sizing.py`, `trading/history.py`, `trading/paper.py`, `notifier/telegram.py`) — when a spot BUY signal fires with STRONG confidence and a BUY position is already open, opens an additional pyramid entry instead of skipping. **8 safety gates**: (1) max entries, (2) ordinal confidence ≥STRONG, (3) min ATR distance from last entry, (4) max % distance from 1st entry, (5a) SL near psychology level, (5b) entry just above round number, (6) entry near S/R, (7) fakeout/rejection wick, (8) aggregate risk cap. Each pyramid entry gets progressively tighter SL (0.8× per level) and its own TP/trailing stop. DB columns `pyramid_entry` + `size_factor`. Terminal shows `[pyramid #N ×50%]` tag; Telegram uses 🧩 icon.
- **`get_open_position_count_by_direction()`** (`trading/history.py`) — returns count of open positions for a given direction and mode.
- **`get_pyramid_size_factor()`** (`signals/sizing.py`) — returns exponential size multiplier for pyramid entry N (1.0 → 0.5 → 0.25 → …).
- **`_get_entry_prices_by_direction()`** (`run_bot.py`) — returns ordered entry prices for open positions, used by distance guards.
- **TA-based pyramid risk gates** (`run_bot.py`) — five new risk checks before pyramiding: (5a) SL near psychology number → stop-hunt risk, (5b) entry just above psychology level → false breakout risk, (6) entry within 1× ATR of resistance (BUY) / support (SELL) → rejection risk, (7) fakeout detection via 24H wick ratio > 60% → reversal signal, (8) aggregate risk cap across all entries (default 5% of account).
- **`_check_psychology_sl_risk()` / `_check_psychology_entry_risk()`** — round-thousand proximity checks.
- **`_check_sr_entry_risk()`** — S/R proximity risk scoring (within 1× ATR = elevated rejection risk).
- **`_detect_fakeout_rejection()`** — 24H wick analysis: if upper wick > 60% of range for BUY, flags fake bullish breakout.
- **`_calc_aggregate_risk()`** — sums risk % weighted by size_factor across all open + new positions.
- **Conviction-based dynamic leverage** (`signals/sizing.py`) — 6-factor model replaces static leverage formula. Leverage now scales with signal confidence: strength ratio vs threshold, HTF alignment, RSI zone confirmation, funding rate, Fear & Greed contrarian, and volatility regime. `_compute_confidence()` returns 0.25–1.5 multiplier. ATR percentile caps max leverage in high-vol regimes (0.33x–1.0x). Effective risk = base_risk × confidence × vol_cap. Tier labels: CONSERVATIVE (≤3x), MODERATE (≤6x), AGGRESSIVE (≤10x).
- **ATR percentile** (`signals/indicators.py`) — `compute_atr_percentile()` ranks current ATR in 100-period history. Used by dynamic leverage.
- **LEVERAGE_CONFIG** (`config.py`) — new config dict for base_max_leverage, atr_lookback, fractional_kelly, confidence bounds.

### Fixed

- **Pyramid confidence check used exact match** (`run_bot.py`) — `!=` comparison meant `min_confidence: "NORMAL"` would skip STRONG signals. Replaced with ordinal ranking (`_CONFIDENCE_LEVEL` + `_confidence_at_least()`): STRONG > NORMAL > WEAK.
- **Post-news strength below threshold still fired** (`signals/engine.py`) — `integrate_news_with_signal()` drains strength (macro -2.0, contradictory news -0.5) but never re-validated against threshold. Now stores `_threshold` in `generate_signals()` and downgrades to HOLD if post-news `strength < threshold`.
- **Futures leverage always 1x** (`signals/sizing.py`) — broken formula `int(1 / (sl_pct * 100))` always returned 0 for realistic stops (>0.5%), clamped to 1x. Replaced with risk-based calculation: `needed_position = risk_amount / sl_distance_pct`.
- **Backtest crash** (`backtest.py`) — `generate_signals()` called without `threshold_override=None`, causing `float >= None` TypeError. Now passes `SIGNAL_THRESHOLD`.
- **Per-mode stats were global** (`trading/history.py`) — `get_outcome_breakdown()`, `get_win_rate()`, `get_profit_factor()` now accept optional `mode` parameter. `print_paper_summary()`, `print_open_status()`, Telegram PERFORMANCE, and combined display all pass per-mode filtering. Previously SPOT and FUTURES showed identical global W/L counts.
- **Negative gap display** — when news downgraded a signal, gap showed as negative (`-2.10 to fire`). Now displays "news downgrade → HOLD" in both terminal and Telegram.

### Changed

- **Telegram notification order** (`run_bot.py`) — main signal alert now sent first, position open/close/warning follow. Previously position notifications fired during Phase 3 before the main signal.
- **Position open gets dedicated Telegram card** (`notifier/telegram.py`) — `_format_open_notification()` sends vertical card with entry, SL%, TP1, TP2, R/R when position opens. Separate from main signal.

### Removed

- **Discord** — `notifier/discord.py` deleted. All Discord references removed from `config.py`, `notifier/common.py`, `notifier/__init__.py`, `notifier.py` shim. Discord webhook logic was unused and added maintenance burden.

---

## 2026-05-08 — Combined Terminal Display + Per-Mode Cleanup

### Added

- **Combined SPOT + FUTURES terminal display** (`signals/terminal.py`) — `display_combined()` replaces two separate `display_analysis()` calls with a single narrow (52-char), fully vertical output. Shared sections (market structure, sentiment, performance) appear once. Mode-specific sections (technicals, HTF, trade setup, reasons) stack vertically per mode. Old `display_analysis()` kept for backward compat with `display=True` kwarg.
- **Mid-cycle check header** (`run_bot.py`) — `run_position_check()` now prints clean separator with BTC price.

### Changed

- **VERDICT section** — terminal and Telegram both use 🔥 (FIRED) / ❄️ (HOLD) icons, buy/sell score breakdown (`B:7.10 · S:2.80`), threshold comparison. Replaced old bar charts and verbose NOTE format.
- **_signal_box()** — boxed verdict now includes buy/sell scores, 🔥/❄️ icons, per-mode max scores.
- **Phase 3 summary** (console) — cleaner header, 🚀 icon for opens, structured block.
- **print_open_status()** — multi-line per position format with SL, TP1, TP2, Trail, Opened.
- **Label consistency** — `FUT 1H` standardized to `FUTURES 1H` across all display sections.

---

## 2026-05-08 — Cron-scheduling fix + heartbeat logs

### Fixed

- **Cron loop skipped cycles after first hour** (`run_bot.py`) — `fired` set was never cleared between hours. After `:01` and `:31` fired, the next hour's `:01` was incorrectly skipped (`1 in fired`). Fixed by clearing `fired` on every wall-clock minute change using a `last_minute` tracker.
- **No heartbeat during wait** — added log messages: "=== Full cycle starting ===" before run_cycle, and "Next run at :XX (~Y min)" after each cycle/check so the log shows the bot is alive between scheduled runs.

---

## 2026-05-07 — SPOT BUY-Only + Clean Vertical Telegram Format

### Changed

- **Consolidated Telegram notifications into single message** (`notifier.py`) — new `_format_consolidated_telegram()` builds one comprehensive message per cycle (was 3-5 separate messages). Sections: Signal Verdicts (both SPOT + FUTURES), Price & Trend, Market Structure, Top Headlines, Performance (all-time paper, per-mode P&L + WR), Position Sizing (SPOT & FUTURES with active trade details or hypothetical SL/TP), NOTE (per-mode verdict summary). Only macro risk banner and position close alerts remain as separate messages.
- **Removed `_format_position_telegram()`** — position/performance summary merged into consolidated message.
- **`_send_combined_telegram()` simplified** — now sends 1 main message + optional macro banner instead of 3-5 separate messages.
- **SPOT pipeline now BUY-only** (`core_analysis.py`) — `generate_signals()` prevents SELL signals when `mode='spot'`. Spot trading cannot short-sell; SELL conditions still show in analysis for informational purposes but are forced to HOLD. A note "SPOT is BUY-only — bearish bias, no SELL opened" appears in signal reasons when bearish conditions dominate.
- **Terminal NOTE section updated** (`core_analysis.py`) — SPOT HOLD with bearish bias now shows "BEARISH" instead of "SELL" as direction, "SPOT is BUY-only" instead of "sell side overrides". WHAT THIS MEANS section clarifies no action is taken on bearish spot signals.
- **Telegram format redesigned — clean vertical layout** (`notifier.py`) — `_format_compact_signal_telegram()` rewritten: every indicator on its own line (`Label    Value`), separate sections for Price & Trend, Technicals, HTF, Market, Sentiment, and Reasons. HTF reads raw indicator dicts for accurate RSI/MACD/Volume per timeframe. Previously all crammed horizontally with `·` separators — now scannable on mobile.
- **Telegram HOLD cards** (`notifier.py`) — spot HOLD shows "BEARISH" as direction (not "SELL"). Futures HOLD unchanged.
- **`run_bot.py`** — comment updated from "max 1 BUY + 1 SELL" to "BUY-only (no short selling on spot)".
- **README** — SPOT BUY-only documented in architecture diagram, position management, and position sizing sections.
- **Local timezone** (`core_analysis.py`) — analysis header now uses system local time (e.g. WIB) instead of hardcoded UTC. `datetime.now().astimezone().strftime(...)`.

### Fixed

- **Telegram not updating** — `send_signal_alert()` was calling `_format_compact_signal_telegram()` (the actual sending function), but the initial rewrite only touched `_format_section_telegram()` which was unused. Second pass rewrote the correct function.

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
