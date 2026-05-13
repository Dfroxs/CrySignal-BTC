"""Signal engine — generate_signals() scoring all 18 conditions, and
integrate_news_with_signal() for macro/sentiment overlay.
"""

import pandas as pd

from config import RISK_CONFIG
from signals.indicators import (calculate_adx, classify_regime,
                                detect_candlestick_pattern, detect_rsi_divergence)
from signals.market_data import get_signal_confidence
from signals.sentiment import check_upcoming_macro_events


def generate_signals(df, htf=None, market_structure=None, sr=None, mode='futures', threshold_override=None):
    current = df.iloc[-1]
    previous = df.iloc[-2]

    atr_stop = current['ATR_14'] * RISK_CONFIG['atr_multiplier']
    threshold = threshold_override if threshold_override is not None else 0

    signal = {
        'type': 'HOLD',
        'strength': 0,
        'reasons': [],
        'entry_price': current['close'],
        'stop_loss': None,
        'take_profit': None,
        'atr': current['ATR_14'],
        'support_resistance': sr or {},
    }

    buy_conditions = 0.0
    sell_conditions = 0.0

    # Pre-compute ADX once — used in condition 3 (MACD gate) and condition 18
    try:
        _adx_df_pre = calculate_adx(df)
        _adx_pre = float(_adx_df_pre['ADX'].iloc[-1])
    except Exception:
        _adx_pre = 0.0

    # 1 — EMA 200 trend + slope (rising EMA = strengthening trend)
    ema200_now  = current['EMA_200']
    ema200_prev = df['EMA_200'].iloc[-6]  # 5-candle slope avoids single-candle noise
    ema_rising  = ema200_now > ema200_prev
    if current['close'] > ema200_now:
        buy_conditions += 1.0 if ema_rising else 0.5
        label = "rising ✓" if ema_rising else "flat/falling ⚠️"
        signal['reasons'].append(f"✓ Price above EMA 200 ({label})")
    else:
        sell_conditions += 1.0 if not ema_rising else 0.5
        label = "falling ✓" if not ema_rising else "flat/rising ⚠️"
        signal['reasons'].append(f"✗ Price below EMA 200 ({label})")

    # 2 — RSI
    rsi = current['RSI_14']
    _rsi_os = False  # track for diminishing-returns check later
    _rsi_ob = False
    if 30 < rsi < 50:
        buy_conditions += 1
        signal['reasons'].append("✓ RSI in buy zone (30–50)")
    elif rsi <= 30:
        buy_conditions += 1.5
        _rsi_os = True
        signal['reasons'].append("✓ RSI OVERSOLD (<30) — strong buy signal")
    elif rsi > 70:
        sell_conditions += 1.5
        _rsi_ob = True
        signal['reasons'].append("✗ RSI OVERBOUGHT (>70) — strong sell signal")
    elif 50 < rsi < 70:
        sell_conditions += 1.0
        signal['reasons'].append("✗ RSI in sell zone (50–70)")

    # 3 — MACD crossover / position (ADX-gated: full weight only in trending markets)
    _macd_cross_w = 1.5 if _adx_pre >= 20 else 0.75
    _macd_low_adx = "" if _adx_pre >= 20 else f" (ADX {_adx_pre:.0f} — reduced)"
    if current['MACD'] > current['MACD_Signal'] and previous['MACD'] <= previous['MACD_Signal']:
        buy_conditions += _macd_cross_w
        signal['reasons'].append(f"✓ MACD bullish crossover{_macd_low_adx}")
    elif current['MACD'] > current['MACD_Signal']:
        buy_conditions += 0.5
        signal['reasons'].append("✓ MACD above signal line")
    elif current['MACD'] < current['MACD_Signal'] and previous['MACD'] >= previous['MACD_Signal']:
        sell_conditions += _macd_cross_w
        signal['reasons'].append(f"✗ MACD bearish crossover{_macd_low_adx}")
    elif current['MACD'] < current['MACD_Signal']:
        sell_conditions += 0.5
        signal['reasons'].append("✗ MACD below signal line")

    # 4 — Volume confirmation
    vol_avg = df['volume'].rolling(20).mean().iloc[-1]
    vol_ratio = current['volume'] / vol_avg if vol_avg > 0 else 1
    if vol_ratio >= 1.3:
        if current['close'] > previous['close']:
            buy_conditions += 1
            signal['reasons'].append(f"✓ Volume confirms UP move ({vol_ratio:.1f}x avg)")
        else:
            sell_conditions += 1
            signal['reasons'].append(f"✗ Volume confirms DOWN move ({vol_ratio:.1f}x avg)")
    elif vol_ratio < 0.7:
        signal['reasons'].append(f"⚠️  Low volume ({vol_ratio:.1f}x avg) — weak conviction")

    # 4b — Volume climax / Effort vs Result (Wyckoff)
    candle_range = current['high'] - current['low']
    range_vs_atr = candle_range / current['ATR_14'] if current['ATR_14'] > 0 else 1
    if vol_ratio >= 1.5 and range_vs_atr < 0.75:
        close_pos = (current['close'] - current['low']) / candle_range if candle_range > 0 else 0.5
        if close_pos < 0.40 and current['close'] >= current['open']:  # green close near low = accumulation
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Effort vs Result ({vol_ratio:.1f}x vol, narrow range) — accumulation")
        elif close_pos > 0.60 and current['close'] <= current['open']:  # red close near high = distribution
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Effort vs Result ({vol_ratio:.1f}x vol, narrow range) — distribution")

    # 5 — Bollinger Bands
    _bb_lower = False
    _bb_upper = False
    if current['close'] <= current['BB_Lower']:
        buy_conditions += 1
        _bb_lower = True
        signal['reasons'].append("✓ Price at/below BB Lower — oversold")
    elif current['close'] >= current['BB_Upper']:
        sell_conditions += 1
        _bb_upper = True
        signal['reasons'].append("✗ Price at/above BB Upper — overbought")
    else:
        # BB middle zone — only score on volatility compression (squeeze)
        bb_mid = current['BB_Middle']
        if bb_mid and bb_mid > 0:
            bb_width_series = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Middle'].replace(0, float('nan'))
            bb_width_now = bb_width_series.iloc[-1]
            valid = bb_width_series.dropna()
            if not pd.isna(bb_width_now) and len(valid) >= 20:
                squeeze = (valid < bb_width_now).mean() <= 0.30  # bottom 30% — compressed
                if squeeze:
                    if current['close'] > bb_mid:
                        buy_conditions += 0.25
                        signal['reasons'].append("✓ BB squeeze + above middle — breakout coil")
                    else:
                        sell_conditions += 0.25
                        signal['reasons'].append("✗ BB squeeze + below middle — breakdown coil")

    # 6 — Multi-timeframe alignment
    if htf:
        htf_keys = [k for k in htf if k != 'aligned' and not k.endswith('_indicators')]
        htf_label = '  '.join(f"{k.upper()}: {htf[k]}" for k in htf_keys)

        ind_keys = [f'{k}_indicators' for k in htf_keys]
        indicators_list = [htf.get(ik, {}) for ik in ind_keys if htf.get(ik)]

        def _htf_score(direction, indicators):
            score = 0.0
            rsi_all_ok = all(
                (direction == 'BUY' and ind.get('rsi_zone') in ('oversold', 'low', 'neutral')) or
                (direction == 'SELL' and ind.get('rsi_zone') in ('overbought', 'elevated', 'neutral'))
                for ind in indicators
            )
            macd_all_ok = all(ind.get('macd') == ('BULLISH' if direction == 'BUY' else 'BEARISH')
                             for ind in indicators)
            vol_rising = any(ind.get('vol_trend') == 'RISING' for ind in indicators)

            if htf['aligned']:
                # Only give the aligned bonus when HTF direction matches the signal direction.
                # Without this check, a bearish-aligned HTF would give buy_add=1.0 just because
                # htf['aligned'] is True, silently suppressing the SELL score via the elif below.
                aligned_dir = htf.get(htf_keys[0], 'NEUTRAL') if htf_keys else 'NEUTRAL'
                dir_matches = (direction == 'BUY' and aligned_dir == 'BULLISH') or \
                              (direction == 'SELL' and aligned_dir == 'BEARISH')
                if dir_matches:
                    if rsi_all_ok:
                        score = 1.5
                    else:
                        score = 1.0
                    if macd_all_ok:
                        score += 0.25
            else:
                # Diverging: structure is weak, only score if RSI is extreme
                for ind in indicators:
                    if direction == 'BUY' and ind.get('rsi_zone') == 'oversold':
                        score += 0.5
                    elif direction == 'SELL' and ind.get('rsi_zone') == 'overbought':
                        score += 0.5

            if vol_rising:
                score += 0.25

            return min(score, 2.0)

        buy_add = _htf_score('BUY', indicators_list)
        sell_add = _htf_score('SELL', indicators_list)

        # Pick the dominant side. The old `elif` caused sell_add to be silently discarded
        # whenever buy_add > 0 (which happened even in bearish HTF due to Bug 1 above).
        if buy_add > sell_add and buy_add > 0:
            buy_conditions += buy_add
            detail = f"HTF{' aligned' if htf['aligned'] else ''}"
            if any(ind.get('rsi_zone') in ('oversold', 'low') for ind in indicators_list):
                detail += " + RSI supports"
            if any(ind.get('macd') == 'BULLISH' for ind in indicators_list):
                detail += " + MACD confirms"
            if any(ind.get('vol_trend') == 'RISING' for ind in indicators_list):
                detail += " + Vol rising"
            signal['reasons'].append(f"✓ {detail} ({htf_label})")
        elif sell_add > buy_add and sell_add > 0:
            sell_conditions += sell_add
            detail = f"HTF{' aligned' if htf['aligned'] else ''}"
            if any(ind.get('rsi_zone') in ('overbought', 'elevated') for ind in indicators_list):
                detail += " + RSI supports"
            if any(ind.get('macd') == 'BEARISH' for ind in indicators_list):
                detail += " + MACD confirms"
            if any(ind.get('vol_trend') == 'RISING' for ind in indicators_list):
                detail += " + Vol rising"
            signal['reasons'].append(f"✗ {detail} ({htf_label})")
        else:
            signal['reasons'].append(f"⚠️  HTF Disagreement ({htf_label}) — caution")

        # Penalty for true HTF conflict (one timeframe BULLISH, other BEARISH).
        # NEUTRAL vs anything is ambiguous — no penalty.
        # Penalises the dominant buy/sell side by -1.0 so borderline signals don't fire
        # against the longer-term trend.
        htf_dirs = [htf.get(k) for k in htf_keys]
        if not htf['aligned'] and 'BULLISH' in htf_dirs and 'BEARISH' in htf_dirs:
            if buy_conditions >= sell_conditions:
                buy_conditions = max(0, buy_conditions - 1.0)
                signal['reasons'].append("⬇ HTF conflict penalty −1.0 (4H vs 1D opposing)")
            else:
                sell_conditions = max(0, sell_conditions - 1.0)
                signal['reasons'].append("⬇ HTF conflict penalty −1.0 (4H vs 1D opposing)")

    # 8 — OBV slope (5-candle)
    obv_slope = df['OBV'].iloc[-1] - df['OBV'].iloc[-5]
    obv_denom = df['volume'].iloc[-5:].sum()
    obv_rel = abs(obv_slope) / obv_denom if obv_denom > 0 else 0
    if obv_rel >= 0.002:
        if obv_slope > 0:
            buy_conditions += 0.75
            signal['reasons'].append("✓ OBV rising — accumulation detected")
        else:
            sell_conditions += 0.75
            signal['reasons'].append("✗ OBV falling — distribution detected")

    # 9 — Market structure (funding + L/S + DXY)
    if market_structure:
        funding = market_structure.get('funding', {})
        ls = market_structure.get('long_short', {})
        dxy = market_structure.get('dxy', {})

        if mode == 'futures':
            _fund_buy = _fund_sell = _ls_buy = _ls_sell = 0
            if funding.get('bias') == 'BULLISH':
                _fund_buy = 0.5
                buy_conditions += _fund_buy
                signal['reasons'].append(f"✓ Funding negative ({funding.get('rate_pct', 0):.4f}%) — shorts dominant")
            elif funding.get('bias') == 'BEARISH':
                _fund_sell = 1.0
                sell_conditions += _fund_sell
                signal['reasons'].append(f"✗ Funding VERY HIGH ({funding.get('rate_pct', 0):.4f}%) — longs overleveraged")
            elif funding.get('bias') == 'SLIGHTLY_BEARISH':
                _fund_sell = 0.25
                sell_conditions += _fund_sell
                signal['reasons'].append(f"✗ Funding elevated ({funding.get('rate_pct', 0):.4f}%)")

            if ls.get('bias') == 'BULLISH':
                _ls_buy = 0.75
                buy_conditions += _ls_buy
                signal['reasons'].append(f"✓ L/S Ratio {ls.get('ratio', 1):.2f} — shorts crowded, squeeze risk")
            elif ls.get('bias') == 'BEARISH':
                _ls_sell = 0.75
                sell_conditions += _ls_sell
                signal['reasons'].append(f"✗ L/S Ratio {ls.get('ratio', 1):.2f} — longs crowded")

            # Tie-breaking: funding vs L/S conflict → net weight to dominant side
            if (_fund_buy and _ls_sell) or (_fund_sell and _ls_buy):
                if (_fund_buy + _ls_buy) > (_fund_sell + _ls_sell):
                    # Buy dominance: remove sell contribution
                    sell_conditions -= (_fund_sell + _ls_sell)
                    signal['reasons'].append(f"⚠️  Funding/LS conflict — buying pressure dominates, sell signals removed")
                elif (_fund_sell + _ls_sell) > (_fund_buy + _ls_buy):
                    buy_conditions -= (_fund_buy + _ls_buy)
                    signal['reasons'].append(f"⚠️  Funding/LS conflict — selling pressure dominates, buy signals removed")

        elif mode == 'spot':
            # Lightweight futures sentiment for spot (informational, half weight)
            if funding.get('bias') == 'BULLISH':
                buy_conditions += 0.25
                signal['reasons'].append(f"✓ Funding negative ({funding.get('rate_pct',0):.4f}%) — bullish futures sentiment")
            elif funding.get('bias') == 'BEARISH':
                sell_conditions += 0.25
                signal['reasons'].append(f"✗ Funding elevated ({funding.get('rate_pct',0):.4f}%) — bearish futures sentiment")

            ls = market_structure.get('long_short', {})
            if ls.get('bias') == 'BULLISH':
                buy_conditions += 0.25
                signal['reasons'].append(f"✓ L/S {ls.get('ratio',1):.2f} — shorts crowded (futures sentiment)")
            elif ls.get('bias') == 'BEARISH':
                sell_conditions += 0.25
                signal['reasons'].append(f"✗ L/S {ls.get('ratio',1):.2f} — longs crowded (futures sentiment)")

        if dxy.get('bias') == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ DXY FALLING ({dxy.get('change_pct', 0):+.2f}%) — weak USD")
        elif dxy.get('bias') == 'BEARISH':
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ DXY RISING ({dxy.get('change_pct', 0):+.2f}%) — strong USD")

    # 10 — S&P 500 trend (0.5 for spot: BTC-SPX correlation weakens in crypto-driven cycles)
    if market_structure:
        sp500 = market_structure.get('sp500', {})
        sp500_weight = 0.5 if mode == 'spot' else 1.0
        if sp500.get('bias') == 'BULLISH':
            buy_conditions += sp500_weight
            signal['reasons'].append(f"✓ S&P500 RISING ({sp500.get('change_pct',0):+.2f}%) — risk-on")
        elif sp500.get('bias') == 'BEARISH':
            sell_conditions += sp500_weight
            signal['reasons'].append(f"✗ S&P500 FALLING ({sp500.get('change_pct',0):+.2f}%) — risk-off")

        # 11 — Stablecoin supply
        stable = market_structure.get('stablecoin', {})
        if stable.get('bias') == 'BULLISH':
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Stablecoin supply RISING (${stable.get('total_b',0):.0f}B) — dry powder")
        elif stable.get('bias') == 'BEARISH':
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Stablecoin supply FALLING (${stable.get('total_b',0):.0f}B) — capital leaving")

        # 12 — BTC Dominance
        btc_dom = market_structure.get('btc_dom', {})
        if btc_dom.get('bias') == 'BULLISH':
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ BTC Dominance RISING ({btc_dom.get('current',0):.1f}%) — BTC inflow")
        elif btc_dom.get('bias') == 'BEARISH':
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ BTC Dominance FALLING ({btc_dom.get('current',0):.1f}%) — BTC outflow")

        # 13 — Open Interest (futures only)
        if mode == 'futures':
            oi = market_structure.get('open_interest', {})
            if oi.get('bias') == 'BULLISH':
                buy_conditions += 0.5
                signal['reasons'].append(f"✓ Open Interest RISING ({oi.get('change_pct',0):+.2f}%) — trend confirmation")
            elif oi.get('bias') == 'BEARISH':
                sell_conditions += 0.5
                signal['reasons'].append(f"✗ Open Interest FALLING ({oi.get('change_pct',0):+.2f}%) — positions closing")

            # 14 — Futures basis (futures only)
            basis_pct = funding.get('basis_pct', 0)
            basis_bias = funding.get('basis_bias', 'NEUTRAL')
            if basis_bias == 'BULLISH':
                buy_conditions += 0.5
                signal['reasons'].append(f"✓ Futures premium ({basis_pct:+.3f}%) — long demand")
            elif basis_bias == 'BEARISH':
                sell_conditions += 0.5
                signal['reasons'].append(f"✗ Futures discount ({basis_pct:+.3f}%) — weak demand")

    # 15 — Stochastic RSI
    sk, sd = current.get('StochRSI_K'), current.get('StochRSI_D')
    psk, psd = previous.get('StochRSI_K'), previous.get('StochRSI_D')
    rsi_now = current.get('RSI_14', 50)
    _stoch_os_cross = False
    _stoch_ob_cross = False
    if all(pd.notna(v) for v in [sk, sd, psk, psd]):
        if sk < 20 and sk > sd and psk <= psd:
            weight = 1.25 if rsi_now < 30 else 1.0
            buy_conditions += weight
            _stoch_os_cross = True
            signal['reasons'].append(f"✓ StochRSI oversold crossover K={sk:.1f} — bullish momentum"
                                     + (" (RSI confirms)" if rsi_now < 30 else ""))
        elif sk > 80 and sk < sd and psk >= psd:
            weight = 1.25 if rsi_now > 70 else 1.0
            sell_conditions += weight
            _stoch_ob_cross = True
            signal['reasons'].append(f"✗ StochRSI overbought crossover K={sk:.1f} — bearish momentum"
                                     + (" (RSI confirms)" if rsi_now > 70 else ""))
        elif sk < 20:
            weight = 0.6 if rsi_now < 30 else 0.5
            buy_conditions += weight
            signal['reasons'].append(f"✓ StochRSI oversold (K={sk:.1f})"
                                     + (" (RSI confirms)" if rsi_now < 30 else ""))
        elif sk > 80:
            weight = 0.6 if rsi_now > 70 else 0.5
            sell_conditions += weight
            signal['reasons'].append(f"✗ StochRSI overbought (K={sk:.1f})"
                                     + (" (RSI confirms)" if rsi_now > 70 else ""))

    # 16 — Support/Resistance proximity (ATR-scaled)
    if sr:
        support = sr.get('support')
        resistance = sr.get('resistance')
        price = current['close']
        atr = current.get('ATR_14', 0)
        # Use 0.2× ATR as proximity threshold (~$135 at $80k BTC) instead of hardcoded 0.3%
        prox_pct = (atr * 0.2) / price if atr > 0 and price > 0 else 0.003
        if support and abs(price - support) / price <= prox_pct and current['close'] >= previous['close']:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Bouncing off support ${support:,.0f} (within {prox_pct*100:.2f}%)")
        elif resistance and abs(price - resistance) / price <= prox_pct and current['close'] <= previous['close']:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Rejected at resistance ${resistance:,.0f} (within {prox_pct*100:.2f}%)")

    # 17 — VWAP crossover (require recent cross — pure position scores nothing in a trending market)
    vwap = current.get('VWAP_24')
    if vwap and not pd.isna(vwap) and len(df) >= 6:
        lookback_close = df['close'].iloc[-6:-1]
        lookback_vwap  = df['VWAP_24'].iloc[-6:-1]
        if current['close'] > vwap:
            if lookback_close.lt(lookback_vwap).any():  # was below VWAP in last 5 candles
                buy_conditions += 0.75
                signal['reasons'].append(f"✓ Price crossed above VWAP ${vwap:,.2f} — institutional buying")
        else:
            if lookback_close.gt(lookback_vwap).any():  # was above VWAP in last 5 candles
                sell_conditions += 0.75
                signal['reasons'].append(f"✗ Price crossed below VWAP ${vwap:,.2f} — institutional selling")

    # 18 — ADX trend strength + DI crossover
    try:
        adx_df = calculate_adx(df)
        adx_val = adx_df['ADX'].iloc[-1]
        di_plus = adx_df['DI+'].iloc[-1]
        di_minus = adx_df['DI-'].iloc[-1]
        prev_di_plus = adx_df['DI+'].iloc[-2]
        prev_di_minus = adx_df['DI-'].iloc[-2]

        if adx_val > 25:
            if di_plus > di_minus:
                buy_conditions += 0.5
                signal['reasons'].append(f"✓ ADX {adx_val:.0f} — trending, DI+/DI- bullish")
            else:
                sell_conditions += 0.5
                signal['reasons'].append(f"✗ ADX {adx_val:.0f} — trending, DI+/DI- bearish")

        # DI crossover (independent trigger)
        if di_plus > di_minus and prev_di_plus <= prev_di_minus:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ DI+ crossed above DI- — bullish momentum (ADX {adx_val:.0f})")
        elif di_minus > di_plus and prev_di_minus <= prev_di_plus:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ DI- crossed above DI+ — bearish momentum (ADX {adx_val:.0f})")

        # Regime classifier
        regime = classify_regime(df, adx_df)
        signal['regime'] = regime['regime']
        signal['adx'] = regime['adx']
        signal['_regime'] = regime
    except Exception:
        regime = {"regime": "UNKNOWN", "threshold_bump": 0, "size_adj": 1.0, "trend_dir": "NEUTRAL"}
        signal['regime'] = "UNKNOWN"

    # ── Gold & VIX correlation ──
    if market_structure:
        gold = market_structure.get("gold", {})
        vix = market_structure.get("vix", {})
        if gold.get("change_pct", 0) > 0.5:
            signal['reasons'].append(f"📊 Gold rising ({gold['change_pct']:+.1f}%) — safe-haven demand")
            if current['close'] < df['close'].iloc[-5]:
                sell_conditions += 0.25  # Gold up + BTC down = risk-off
        elif gold.get("change_pct", 0) < -0.5:
            buy_conditions += 0.25
            signal['reasons'].append(f"📊 Gold falling ({gold['change_pct']:+.1f}%) — risk-on, capital rotating to crypto")
        if vix.get("change_pct", 0) > 3:
            buy_conditions += 0.25
            signal['reasons'].append(f"📊 VIX spiking ({vix['change_pct']:+.1f}%) — fear gauge, contrarian BTC bid")

    # ── Session-based threshold adjustment ──
    from datetime import UTC, datetime
    utc_hour = datetime.now(UTC).hour
    if utc_hour < 8:
        session_bump = 0.5    # Asia session: low liquidity, raise threshold
    elif 13 <= utc_hour < 22:
        session_bump = -0.25  # US session: highest volume, lower threshold
    else:
        session_bump = 0.0
    # Floor at 3.0 so session/regime bumps can't push threshold below the spot minimum.
    threshold = max(threshold + regime.get("threshold_bump", 0) + session_bump, 3.0)

    # ── OI × Price directional analysis ──
    if mode == 'futures' and market_structure:
        oi = market_structure.get('open_interest', {})
        oi_change = oi.get('change_pct', 0)
        price_change = (current['close'] - df['close'].iloc[-5]) / df['close'].iloc[-5] * 100
        if oi_change > 1 and price_change > 0.5:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ OI↑ ({oi_change:+.1f}%) + Price↑ — healthy uptrend")
        elif oi_change > 1 and price_change < -0.5:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ OI↑ ({oi_change:+.1f}%) + Price↓ — distribution")
        elif oi_change < -1 and price_change > 0.5:
            buy_conditions += 0.25
            signal['reasons'].append(f"⚠️  OI↓ ({oi_change:+.1f}%) + Price↑ — short squeeze")
        elif oi_change < -1 and price_change < -0.5:
            sell_conditions += 0.5
            signal['reasons'].append(f"✓ OI↓ ({oi_change:+.1f}%) + Price↓ — liquidation cascade")

    # ── Diminishing returns on correlated oversold/overbought conditions ──
    # RSI OS/OB, BB lower/upper, StochRSI OS/OB crossover all fire from the
    # same price extreme.  First condition = full weight, second = 0.5×, third = 0.25×.
    buy_extremes = sum([_rsi_os, _bb_lower, _stoch_os_cross])
    sell_extremes = sum([_rsi_ob, _bb_upper, _stoch_ob_cross])
    if buy_extremes >= 2:
        penalty = (buy_extremes - 1) * 0.75  # 2→-0.75, 3→-1.5
        buy_conditions -= penalty
        signal['reasons'].append(f"⚠️  {buy_extremes} oversold conditions clustered — diminishing returns applied (-{penalty:.2f})")
    if sell_extremes >= 2:
        penalty = (sell_extremes - 1) * 0.75
        sell_conditions -= penalty
        signal['reasons'].append(f"⚠️  {sell_extremes} overbought conditions clustered — diminishing returns applied (-{penalty:.2f})")

    # 7 — RSI Divergence (scored after diminishing-returns penalty — structurally independent)
    divergence = detect_rsi_divergence(df)
    signal['rsi_divergence'] = divergence
    if divergence == 'BULLISH' and _rsi_ob:
        sell_conditions -= 1.5  # cancel OB sell score
        buy_conditions += 1.5   # restore divergence advantage
        signal['reasons'].append("⚠️  RSI OB cancelled by BULLISH divergence — divergence takes precedence")
    elif divergence == 'BEARISH' and _rsi_os:
        buy_conditions -= 1.5   # cancel OS buy score
        sell_conditions += 1.5
        signal['reasons'].append("⚠️  RSI OS cancelled by BEARISH divergence — divergence takes precedence")
    elif divergence == 'BULLISH':
        buy_conditions += 2.0
        signal['reasons'].append("✓ RSI BULLISH DIVERGENCE — price lower low, RSI higher low")
    elif divergence == 'BEARISH':
        sell_conditions += 2.0
        signal['reasons'].append("✗ RSI BEARISH DIVERGENCE — price higher high, RSI lower high")

    # 19 — Candlestick pattern recognition
    cs = detect_candlestick_pattern(df) or {}
    signal['candlestick'] = cs
    _cs_weights = {'ENGULFING': 1.0, 'MORNING_STAR': 1.0, 'EVENING_STAR': 1.0,
                   'HAMMER': 0.75, 'SHOOTING_STAR': 0.75, 'HARAMI': 0.5}
    bullish_cs = cs.get('bullish')
    bearish_cs = cs.get('bearish')
    if bullish_cs:
        w = _cs_weights.get(bullish_cs, 0.5)
        buy_conditions += w
        signal['reasons'].append(f"✓ {bullish_cs.replace('_', ' ')} pattern — bullish reversal")
    if bearish_cs and mode == 'futures':
        w = _cs_weights.get(bearish_cs, 0.5)
        sell_conditions += w
        signal['reasons'].append(f"✗ {bearish_cs.replace('_', ' ')} pattern — bearish reversal")

    # 20 — MFI (Money Flow Index) — volume-weighted RSI, detects institutional flow
    mfi = current.get('MFI_14')
    if mfi is not None and not pd.isna(mfi):
        if mfi <= 20:
            buy_conditions += 1.5
            signal['reasons'].append(f"✓ MFI OVERSOLD ({mfi:.0f}) — strong buying pressure")
        elif mfi <= 40:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ MFI in buy zone ({mfi:.0f}) — accumulation building")
        elif mfi >= 80:
            sell_conditions += 1.5
            signal['reasons'].append(f"✗ MFI OVERBOUGHT ({mfi:.0f}) — strong selling pressure")
        elif mfi >= 60:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ MFI in sell zone ({mfi:.0f}) — distribution detected")

    # 21 — CMF (Chaikin Money Flow) — accumulation/distribution pressure
    cmf = current.get('CMF_20')
    if cmf is not None and not pd.isna(cmf):
        if cmf >= 0.25:
            buy_conditions += 1.0
            signal['reasons'].append(f"✓ CMF strong accumulation ({cmf:+.3f}) — institutional buying")
        elif cmf >= 0.10:
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ CMF mild accumulation ({cmf:+.3f})")
        elif cmf <= -0.25:
            sell_conditions += 1.0
            signal['reasons'].append(f"✗ CMF strong distribution ({cmf:+.3f}) — institutional selling")
        elif cmf <= -0.10:
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ CMF mild distribution ({cmf:+.3f})")

    # 22 — Taker buy/sell ratio (futures only) — aggressive order flow dominance
    if mode == 'futures' and market_structure:
        taker = market_structure.get('taker', {})
        if taker.get('bias') == 'BULLISH':
            buy_conditions += 1.0
            signal['reasons'].append(f"✓ Taker ratio {taker.get('ratio', 0):.2f} — aggressive buyers dominant")
        elif taker.get('bias') == 'BEARISH':
            sell_conditions += 1.0
            signal['reasons'].append(f"✗ Taker ratio {taker.get('ratio', 0):.2f} — aggressive sellers dominant")

    # Determine final signal
    if buy_conditions >= threshold and buy_conditions > sell_conditions:
        signal['type'] = 'BUY'
        signal['strength'] = buy_conditions
        signal['stop_loss'] = current['close'] - atr_stop
        raw_tp = current['close'] + (atr_stop * RISK_CONFIG['take_profit_rr'])
        if sr and sr.get('resistance'):
            dist_sr = sr['resistance'] - current['close']
            if dist_sr > 0 and dist_sr < (raw_tp - current['close']) * 0.85:
                capped_tp = sr['resistance'] * 0.995
                if (capped_tp - current['close']) / atr_stop >= 1.0:
                    signal['reasons'].append(f"⚠️  TP capped at resistance ${sr['resistance']:,.0f}")
                    raw_tp = capped_tp
        signal['take_profit'] = raw_tp
    elif mode != 'spot' and sell_conditions >= threshold and sell_conditions > buy_conditions:
        signal['type'] = 'SELL'
        signal['strength'] = sell_conditions
        signal['stop_loss'] = current['close'] + atr_stop
        raw_tp = current['close'] - (atr_stop * RISK_CONFIG['take_profit_rr'])
        if sr and sr.get('support'):
            dist_sr = current['close'] - sr['support']
            if dist_sr > 0 and dist_sr < (current['close'] - raw_tp) * 0.85:
                capped_tp = sr['support'] * 1.005
                if (current['close'] - capped_tp) / atr_stop >= 1.0:
                    signal['reasons'].append(f"⚠️  TP capped at support ${sr['support']:,.0f}")
                    raw_tp = capped_tp
        signal['take_profit'] = raw_tp
    else:
        signal['type'] = 'HOLD'
        signal['strength'] = max(buy_conditions, sell_conditions)
        if mode == 'spot' and sell_conditions > buy_conditions:
            signal['reasons'].append("ℹ️  SPOT is BUY-only — bearish bias, no SELL opened")

    signal['buy_score'] = round(buy_conditions, 2)
    signal['sell_score'] = round(sell_conditions, 2)
    signal['atr'] = current['ATR_14']
    signal['_threshold'] = threshold

    # TP2 = 2× the TP1 distance, capped below nearest resistance (BUY) or above support (SELL)
    if signal['type'] != 'HOLD' and signal['take_profit'] is not None:
        tp1_dist = abs(signal['take_profit'] - signal['entry_price'])
        sr_levels = signal.get('support_resistance') or {}
        if signal['type'] == 'BUY':
            tp2_raw = signal['entry_price'] + tp1_dist * 2
            resistance = sr_levels.get('resistance')
            if resistance and signal['entry_price'] < resistance < tp2_raw:
                capped = resistance * 0.995
                if capped > signal['take_profit']:  # only cap if capped value still exceeds TP1
                    tp2_raw = capped
            signal['tp2'] = round(tp2_raw, 2)
        else:
            tp2_raw = signal['entry_price'] - tp1_dist * 2
            support = sr_levels.get('support')
            if support and signal['entry_price'] > support > tp2_raw:
                capped = support * 1.005
                if capped < signal['take_profit']:  # only cap if capped value still exceeds TP1
                    tp2_raw = capped
            signal['tp2'] = round(tp2_raw, 2)

    if signal['type'] != 'HOLD':
        signal['confidence'] = get_signal_confidence(signal['strength'], threshold)
    else:
        signal['confidence'] = None

    return signal


def integrate_news_with_signal(signal, news_data):
    enhanced = signal.copy()

    has_macro, event_name = check_upcoming_macro_events()
    if has_macro:
        enhanced['strength'] = max(0, enhanced['strength'] - 2.0)
        enhanced['reasons'].append(f"⚠️  MACRO CAUTION: HIGH impact event in <2h ({event_name}) — strength -2.0")
        if enhanced['strength'] <= 0:
            enhanced['type'] = 'HOLD'

    fng = news_data.get('fear_greed', {})
    fng_val = fng.get('value', 50)

    if fng_val <= 20 and enhanced['type'] == 'BUY':
        enhanced['strength'] += 1.5
        enhanced['reasons'].append(f"🔴 EXTREME FEAR ({fng_val}) — contrarian BUY confirmed")
    elif fng_val >= 80 and enhanced['type'] == 'SELL':
        enhanced['strength'] += 1.5
        enhanced['reasons'].append(f"🟢 EXTREME GREED ({fng_val}) — contrarian SELL confirmed")
    elif fng_val >= 70 and enhanced['type'] == 'BUY':
        # Buying into greed — risky, not contrarian
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  F&G GREED ({fng_val}) contradicts BUY — chasing risk")
    elif fng_val <= 30 and enhanced['type'] == 'SELL':
        # Selling into fear — risky, not contrarian
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  F&G FEAR ({fng_val}) contradicts SELL — panic risk")
    elif news_data.get('sentiment') == 'BULLISH' and enhanced['type'] == 'BUY':
        enhanced['strength'] += 0.75
        enhanced['reasons'].append(f"📰 Sentiment BULLISH (F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BEARISH' and enhanced['type'] == 'SELL':
        enhanced['strength'] += 0.75
        enhanced['reasons'].append(f"📰 Sentiment BEARISH (F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BEARISH' and enhanced['type'] == 'BUY':
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  Sentiment contradicts (BEARISH, F&G: {fng_val})")
    elif news_data.get('sentiment') == 'BULLISH' and enhanced['type'] == 'SELL':
        enhanced['strength'] = max(0, enhanced['strength'] - 0.5)
        enhanced['reasons'].append(f"⚠️  Sentiment contradicts (BULLISH, F&G: {fng_val})")

    enhanced['news_sentiment'] = news_data.get('sentiment', 'NEUTRAL')
    enhanced['news_confidence'] = news_data.get('confidence', 0)
    enhanced['fear_greed_value'] = fng_val
    enhanced['fear_greed_label'] = fng.get('label', 'Neutral')

    # Re-validate: if post-news strength dropped below threshold, downgrade to HOLD
    thr = enhanced.get('_threshold', 0)
    if enhanced['type'] != 'HOLD' and enhanced['strength'] < thr:
        enhanced['type'] = 'HOLD'
        enhanced['confidence'] = None
        enhanced['reasons'].append(
            f"⚠️  Post-news strength ({enhanced['strength']:.2f}) below threshold ({thr:.2f}) — downgraded to HOLD"
        )

    # Always recalculate confidence after news adjustments — strength may have changed
    if enhanced['type'] != 'HOLD':
        enhanced['confidence'] = get_signal_confidence(enhanced['strength'], thr)

    return enhanced
