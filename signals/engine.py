"""Signal engine — generate_signals() scoring all 18 conditions, and
integrate_news_with_signal() for macro/sentiment overlay.
"""

import pandas as pd

from config import RISK_CONFIG
from signals.indicators import detect_rsi_divergence
from signals.market_data import get_signal_confidence
from signals.sentiment import check_upcoming_macro_events


def generate_signals(df, htf=None, market_structure=None, sr=None, mode='futures', threshold_override=None):
    current = df.iloc[-1]
    previous = df.iloc[-2]

    atr_stop = current['ATR_14'] * RISK_CONFIG['atr_multiplier']
    threshold = threshold_override

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

    # 1 — EMA 200 trend
    if current['close'] > current['EMA_200']:
        buy_conditions += 1
        signal['reasons'].append("✓ Price above EMA 200 (bullish trend)")
    else:
        sell_conditions += 1
        signal['reasons'].append("✗ Price below EMA 200 (bearish trend)")

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
    elif rsi > 55:
        sell_conditions += 0.5
        signal['reasons'].append("✗ RSI elevated (>55)")

    # 3 — MACD crossover / position
    if current['MACD'] > current['MACD_Signal'] and previous['MACD'] <= previous['MACD_Signal']:
        buy_conditions += 1.5
        signal['reasons'].append("✓ MACD bullish crossover")
    elif current['MACD'] > current['MACD_Signal']:
        buy_conditions += 0.5
        signal['reasons'].append("✓ MACD above signal line")
    elif current['MACD'] < current['MACD_Signal'] and previous['MACD'] >= previous['MACD_Signal']:
        sell_conditions += 1.5
        signal['reasons'].append("✗ MACD bearish crossover")
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
    if vol_ratio >= 2.0 and range_vs_atr < 0.5:
        close_pos = (current['close'] - current['low']) / candle_range if candle_range > 0 else 0.5
        if close_pos < 0.35:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Volume climax ({vol_ratio:.1f}x) + narrow range — potential accumulation")
        elif close_pos > 0.65:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Volume climax ({vol_ratio:.1f}x) + narrow range — potential distribution")

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
    elif current['close'] > current['BB_Middle']:
        buy_conditions += 0.25
    else:
        sell_conditions += 0.25

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
                if rsi_all_ok:
                    score = 1.5
                else:
                    score = 0.75
                if macd_all_ok:
                    score += 0.25
            else:
                for ind in indicators:
                    if direction == 'BUY' and ind.get('rsi_zone') == 'oversold':
                        score += 0.75
                    elif direction == 'SELL' and ind.get('rsi_zone') == 'overbought':
                        score += 0.75

            if vol_rising:
                score += 0.25

            return min(score, 2.0)

        buy_add = _htf_score('BUY', indicators_list)
        sell_add = _htf_score('SELL', indicators_list)

        if buy_add > 0:
            buy_conditions += buy_add
            detail = f"HTF{' aligned' if htf['aligned'] else ''}"
            if any(ind.get('rsi_zone') in ('oversold', 'low') for ind in indicators_list):
                detail += " + RSI supports"
            if any(ind.get('macd') == 'BULLISH' for ind in indicators_list):
                detail += " + MACD confirms"
            if any(ind.get('vol_trend') == 'RISING' for ind in indicators_list):
                detail += " + Vol rising"
            signal['reasons'].append(f"✓ {detail} ({htf_label})")
        elif sell_add > 0:
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

    # 7 — RSI Divergence
    divergence = detect_rsi_divergence(df)
    signal['rsi_divergence'] = divergence
    if divergence == 'BULLISH':
        buy_conditions += 2.0
        signal['reasons'].append("✓ RSI BULLISH DIVERGENCE — price lower low, RSI higher low")
    elif divergence == 'BEARISH':
        sell_conditions += 2.0
        signal['reasons'].append("✗ RSI BEARISH DIVERGENCE — price higher high, RSI lower high")

    # 8 — OBV slope (5-candle)
    obv_slope = df['OBV'].iloc[-1] - df['OBV'].iloc[-5]
    obv_denom = df['volume'].iloc[-5:].sum()
    obv_rel = abs(obv_slope) / obv_denom if obv_denom > 0 else 0
    if obv_rel >= 0.001:
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
            if funding.get('bias') == 'BULLISH':
                buy_conditions += 0.5
                signal['reasons'].append(f"✓ Funding negative ({funding.get('rate_pct', 0):.4f}%) — shorts dominant")
            elif funding.get('bias') == 'BEARISH':
                sell_conditions += 1.0
                signal['reasons'].append(f"✗ Funding VERY HIGH ({funding.get('rate_pct', 0):.4f}%) — longs overleveraged")
            elif funding.get('bias') == 'SLIGHTLY_BEARISH':
                sell_conditions += 0.25
                signal['reasons'].append(f"✗ Funding elevated ({funding.get('rate_pct', 0):.4f}%)")

            if ls.get('bias') == 'BULLISH':
                buy_conditions += 0.75
                signal['reasons'].append(f"✓ L/S Ratio {ls.get('ratio', 1):.2f} — shorts crowded, squeeze risk")
            elif ls.get('bias') == 'BEARISH':
                sell_conditions += 0.75
                signal['reasons'].append(f"✗ L/S Ratio {ls.get('ratio', 1):.2f} — longs crowded")

        if dxy.get('bias') == 'BULLISH':
            buy_conditions += 0.5
            signal['reasons'].append(f"✓ DXY FALLING ({dxy.get('change_pct', 0):+.2f}%) — weak USD")
        elif dxy.get('bias') == 'BEARISH':
            sell_conditions += 0.5
            signal['reasons'].append(f"✗ DXY RISING ({dxy.get('change_pct', 0):+.2f}%) — strong USD")

    # 10 — S&P 500 trend
    if market_structure:
        sp500 = market_structure.get('sp500', {})
        if sp500.get('bias') == 'BULLISH':
            buy_conditions += 1.0
            signal['reasons'].append(f"✓ S&P500 RISING ({sp500.get('change_pct',0):+.2f}%) — risk-on")
        elif sp500.get('bias') == 'BEARISH':
            sell_conditions += 1.0
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

    # 16 — Support/Resistance proximity
    if sr:
        support = sr.get('support')
        resistance = sr.get('resistance')
        price = current['close']
        prox = 0.003
        if support and abs(price - support) / price <= prox and current['close'] >= previous['close']:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Bouncing off support ${support:,.0f}")
        elif resistance and abs(price - resistance) / price <= prox and current['close'] <= previous['close']:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Rejected at resistance ${resistance:,.0f}")

    # 17 — VWAP position
    vwap = current.get('VWAP_24')
    if vwap and not pd.isna(vwap):
        if current['close'] > vwap:
            buy_conditions += 0.75
            signal['reasons'].append(f"✓ Price above VWAP ${vwap:,.2f} — institutional buying")
        else:
            sell_conditions += 0.75
            signal['reasons'].append(f"✗ Price below VWAP ${vwap:,.2f} — institutional selling")

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

    # ── RSI divergence overrides RSI zone (they contradict) ──
    if divergence == 'BULLISH' and _rsi_ob:
        # Bullish divergence + overbought RSI: divergence wins, suppress OB score
        buy_conditions += 1.5   # was already suppressed by OB; restore divergence advantage
        signal['reasons'].append("⚠️  RSI OB suppressed by BULLISH divergence — divergence stronger signal")
    elif divergence == 'BEARISH' and _rsi_os:
        sell_conditions += 1.5
        signal['reasons'].append("⚠️  RSI OS suppressed by BEARISH divergence — divergence stronger signal")

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

    # TP2 = 2× the TP1 distance
    if signal['type'] != 'HOLD' and signal['take_profit'] is not None:
        tp1_dist = abs(signal['take_profit'] - signal['entry_price'])
        if signal['type'] == 'BUY':
            signal['tp2'] = signal['entry_price'] + tp1_dist * 2
        else:
            signal['tp2'] = signal['entry_price'] - tp1_dist * 2

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

    return enhanced
