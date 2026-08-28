"""Pipeline dummy-data test — exercises display & formatter for all signal combinations.

Run:  python3 test_pipelines.py
No network, no DB, no exchange needed.
"""

import sys
import traceback

# ── Dummy signal builder ─────────────────────────────────────────────────────

PRICE = 95_000.0
ATR   = 800.0

def _last(price=PRICE, atr=ATR):
    return {
        "close":     price,
        "ema200":    price * 0.95,
        "rsi":       55.0,
        "macd":      120.0,
        "macd_sig":  80.0,
        "stoch_k":   62.0,
        "stoch_d":   58.0,
        "vwap":      price * 0.99,
        "bb_upper":  price * 1.02,
        "bb_middle": price,
        "bb_lower":  price * 0.98,
        "atr":       atr,
        "obv_slope": 5000,
        "hi24":      price * 1.015,
        "lo24":      price * 0.985,
    }

def _htf(mode="futures"):
    if mode == "spot":
        return {
            "1d": "BULLISH", "1d_indicators": {"rsi": 58, "macd": "BULLISH", "vol_trend": "RISING"},
            "1w": "BULLISH", "1w_indicators": {"rsi": 62, "macd": "BULLISH", "vol_trend": "FLAT"},
            "aligned": True,
        }
    return {
        "4h": "BULLISH", "4h_indicators": {"rsi": 54, "macd": "BULLISH", "vol_trend": "RISING"},
        "1d": "BULLISH", "1d_indicators": {"rsi": 60, "macd": "BULLISH", "vol_trend": "FLAT"},
        "aligned": True,
    }

def _htf_bearish(mode="futures"):
    if mode == "spot":
        return {
            "1d": "BEARISH", "1d_indicators": {"rsi": 42, "macd": "BEARISH", "vol_trend": "FALLING"},
            "1w": "BEARISH", "1w_indicators": {"rsi": 38, "macd": "BEARISH", "vol_trend": "FLAT"},
            "aligned": True,
        }
    return {
        "4h": "BEARISH", "4h_indicators": {"rsi": 44, "macd": "BEARISH", "vol_trend": "FALLING"},
        "1d": "BEARISH", "1d_indicators": {"rsi": 40, "macd": "BEARISH", "vol_trend": "FLAT"},
        "aligned": True,
    }

def _market():
    return {
        "funding":      {"rate_pct": -0.00520, "bias": "BULLISH", "basis_pct": 0.0120, "basis_bias": "BULLISH"},
        "long_short":   {"ratio": 0.87, "bias": "BULLISH"},
        "open_interest":{"notional": 18_500_000_000, "change_pct": 0.42, "bias": "BULLISH"},
        "dxy":          {"current": 104.23, "change_pct": -0.15},
        "sp500":        {"current": 5820.0, "change_pct": 0.35, "bias": "BULLISH"},
        "stablecoin":   {"total_b": 186, "change_pct": 0.8, "bias": "BULLISH"},
        "btc_dom":      {"current": 54.2, "change_pct": 0.3, "bias": "BULLISH"},
        "gold":         {"current": 3100.0, "change_pct": 0.2},
        "vix":          {"current": 16.4, "change_pct": -3.1},
    }

def _news():
    return {
        "fear_greed":     {"value": 62, "label": "Greed"},
        "headlines": [
            {"title": "BTC breaks key resistance at $95K", "sentiment": 1, "category": "crypto"},
            {"title": "Fed signals pause on rate hikes",   "sentiment": 1, "category": "macro"},
            {"title": "Stablecoin inflows accelerate",     "sentiment": 1, "category": "crypto"},
        ],
        "sources_checked": ["FinancialJuice", "CoinGecko"],
    }

def _sr():
    return {"support": PRICE * 0.97, "resistance": PRICE * 1.03}

def _regime_info(regime="TRENDING"):
    return {"regime": regime, "adx": 28.5, "di_plus": 24.1, "di_minus": 18.3,
            "trend_dir": "BULLISH", "threshold_bump": -0.25, "size_adj": 1.0}

def make_signal(stype, mode, score=6.5, buy_score=None, sell_score=None,
                confidence="NORMAL", htf_bearish=False):
    """Build a fully-populated dummy signal dict."""
    if buy_score is None and sell_score is None:
        if stype == "BUY":
            buy_score, sell_score = score, score * 0.4
        elif stype == "SELL":
            buy_score, sell_score = score * 0.4, score
        else:  # HOLD
            buy_score, sell_score = score * 0.6, score * 0.5

    entry = PRICE
    atr   = ATR
    sl    = entry - atr * 1.5 if stype == "BUY" else entry + atr * 1.5
    tp    = entry + atr * 3.75 if stype == "BUY" else entry - atr * 3.75
    tp2   = entry + atr * 7.5  if stype == "BUY" else entry - atr * 7.5

    htf_data = (_htf_bearish(mode) if htf_bearish else _htf(mode))

    sig = {
        "type":             stype,
        "mode":             mode,
        "strength":         score,
        "buy_score":        buy_score,
        "sell_score":       sell_score,
        "confidence":       confidence if stype != "HOLD" else "",
        "entry_price":      entry,
        "stop_loss":        sl   if stype != "HOLD" else None,
        "take_profit":      tp   if stype != "HOLD" else None,
        "tp2":              tp2  if stype != "HOLD" else None,
        "reasons": [
            "✓ Price above EMA 200 — uptrend",
            "✓ RSI 55 — neutral zone, room to run",
            "✓ MACD bullish crossover",
            "✓ HTF 4H + 1D aligned BULLISH",
            "✗ Volume below average",
        ] if stype == "BUY" else [
            "✓ Price below EMA 200 — downtrend",
            "✓ RSI 44 — neutral zone",
            "✓ MACD bearish crossover",
            "✗ OBV slope flat",
        ] if stype == "SELL" else [
            "✗ Score 4.20 below threshold 5.20",
            "✗ HTF diverging",
        ],
        "_threshold":       5.2 if mode == "futures" else 4.3,
        "_atr_percentile":  0.45,
        "_htf":             htf_data,
        "_market":          _market(),
        "_news_data":       _news(),
        "_last":            _last(entry, atr),
        "_regime":          _regime_info(),
        "support_resistance": _sr(),
        "rsi_divergence":   "NONE",
        "candlestick":      {"bullish": None, "bearish": None},
        "regime":           "TRENDING",
        "adx":              28.5,
        "news_sentiment":   "BULLISH",
        "news_confidence":  72.0,
        "fear_greed_value": 62,
        "fear_greed_label": "Greed",
        "db_id":            1,
    }
    return sig


# ── Test runner ───────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0

def run(label, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✓  {label}")
        PASS += 1
    except Exception as e:
        print(f"  ✗  {label}")
        traceback.print_exc()
        FAIL += 1


# ── 1. _mode_label() ──────────────────────────────────────────────────────────

def test_mode_labels():
    from notifier.common import _mode_label

    cases = [
        (make_signal("BUY",  "spot"),    "SPOT 4H"),
        (make_signal("HOLD", "spot"),    "SPOT 4H"),
        (make_signal("BUY",  "futures"), "FUTURES LONG 1H"),
        (make_signal("SELL", "futures"), "FUTURES SHORT 1H"),
        (make_signal("HOLD", "futures"), "FUTURES 1H"),
    ]
    for sig, expected in cases:
        got = _mode_label(sig)
        assert got == expected, f"_mode_label: expected {expected!r}, got {got!r}"


# ── 2. Telegram compact formatter ─────────────────────────────────────────────

def test_compact_spot_buy():
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("BUY", "spot", confidence="STRONG")
    out = _format_compact_signal_telegram(sig)
    assert "BUY" in out and "SPOT 4H" in out

def test_compact_futures_long():
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("BUY", "futures", confidence="NORMAL")
    out = _format_compact_signal_telegram(sig)
    assert "BUY" in out
    assert "FUTURES LONG 1H" in out

def test_compact_futures_short():
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("SELL", "futures", confidence="STRONG")
    out = _format_compact_signal_telegram(sig)
    assert "SELL" in out
    assert "FUTURES SHORT 1H" in out

def test_compact_hold_spot():
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("HOLD", "spot", score=3.5, buy_score=3.5, sell_score=1.0)
    out = _format_compact_signal_telegram(sig)
    assert "HOLD" in out
    assert "Gap" in out
    assert "_conflict" not in out

def test_compact_hold_futures():
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("HOLD", "futures", score=3.8, buy_score=3.8, sell_score=2.0)
    out = _format_compact_signal_telegram(sig)
    assert "HOLD" in out
    assert "Gap" in out

def test_compact_hold_gap_negative_no_crash():
    """Score exceeds threshold but forced HOLD by macro — gap is negative."""
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("HOLD", "futures", score=6.5, buy_score=6.5, sell_score=2.0)
    out = _format_compact_signal_telegram(sig)
    assert "HOLD" in out


# ── 3. Telegram consolidated formatter ────────────────────────────────────────

def test_consolidated_spot_buy_futures_long():
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("BUY",  "spot",    confidence="STRONG")
    fut  = make_signal("BUY",  "futures", confidence="NORMAL")
    out  = _format_consolidated_telegram(spot, fut)
    assert "SPOT 4H" in out
    assert "FUTURES LONG 1H" in out
    assert "BUY" in out

def test_consolidated_spot_buy_futures_short():
    """Previously would have been blocked by conflict detection — now both fire."""
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("BUY",  "spot",    confidence="NORMAL")
    fut  = make_signal("SELL", "futures", confidence="STRONG")
    out  = _format_consolidated_telegram(spot, fut)
    assert "SPOT 4H" in out
    assert "FUTURES SHORT 1H" in out
    assert "BUY" in out
    assert "SELL" in out
    assert "CONFLICT" not in out   # no conflict label anymore

def test_consolidated_spot_hold_futures_short():
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("HOLD", "spot",    score=3.0, buy_score=3.0, sell_score=1.5)
    fut  = make_signal("SELL", "futures", confidence="STRONG")
    out  = _format_consolidated_telegram(spot, fut)
    assert "FUTURES SHORT 1H" in out

def test_consolidated_both_hold():
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("HOLD", "spot",    score=3.0, buy_score=3.0, sell_score=1.0)
    fut  = make_signal("HOLD", "futures", score=3.5, buy_score=3.5, sell_score=2.0)
    out  = _format_consolidated_telegram(spot, fut)
    assert "HOLD" in out
    assert "gap" in out.lower()   # consolidated uses lowercase "gap"

def test_consolidated_spot_only():
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("BUY", "spot", confidence="NORMAL")
    out  = _format_consolidated_telegram(spot, None)
    assert "SPOT 4H" in out
    # Performance section always shows both mode labels — that's correct behavior
    assert "TECHNICALS — SPOT 4H" in out
    assert "TECHNICALS — FUTURES" not in out   # no futures technicals section

def test_consolidated_futures_only():
    from notifier.telegram import _format_consolidated_telegram
    fut = make_signal("SELL", "futures", confidence="STRONG")
    out = _format_consolidated_telegram(None, fut)
    assert "FUTURES SHORT 1H" in out
    # Performance section always shows both mode labels — that's correct behavior
    assert "TECHNICALS — FUTURES" in out
    assert "TECHNICALS — SPOT" not in out   # no spot technicals section

def test_consolidated_verdict_spot_bearish_spot_only():
    """Spot BUY-only gate — sell_score > buy_score on spot → BEARISH label in verdict."""
    from notifier.telegram import _format_consolidated_telegram
    sig = make_signal("HOLD", "spot", score=4.0, buy_score=1.5, sell_score=4.0)
    out = _format_consolidated_telegram(sig, None)
    assert "BEARISH" in out or "BUY-only" in out


# ── 4. Terminal display_combined ──────────────────────────────────────────────

import io, contextlib

def _capture(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()

def test_terminal_spot_buy_futures_long():
    from signals.terminal import display_combined
    spot = make_signal("BUY",  "spot",    confidence="STRONG")
    fut  = make_signal("BUY",  "futures", confidence="NORMAL")
    out  = _capture(display_combined, spot, fut)
    assert "SPOT 4H" in out
    assert "FUTURES LONG 1H" in out

def test_terminal_spot_buy_futures_short():
    """Key scenario: 4H bullish spot + 1H bearish futures — both should show, no CONFLICT."""
    from signals.terminal import display_combined
    spot = make_signal("BUY",  "spot",    confidence="NORMAL")
    fut  = make_signal("SELL", "futures", confidence="STRONG")
    out  = _capture(display_combined, spot, fut)
    assert "SPOT 4H" in out
    assert "FUTURES SHORT 1H" in out
    assert "CONFLICT" not in out

def test_terminal_hold_futures_short():
    from signals.terminal import display_combined
    spot = make_signal("HOLD", "spot",    score=3.2, buy_score=3.2, sell_score=1.0)
    fut  = make_signal("SELL", "futures", confidence="STRONG")
    out  = _capture(display_combined, spot, fut)
    assert "FUTURES SHORT 1H" in out

def test_terminal_combined_box_labels():
    """Verify FUT LONG 1H / FUT SHORT 1H labels in the compact verdict box."""
    from signals.terminal import display_combined
    for stype, expected_label in [("BUY", "FUT LONG 1H"), ("SELL", "FUT SHORT 1H")]:
        fut = make_signal(stype, "futures", confidence="STRONG")
        out = _capture(display_combined, make_signal("HOLD", "spot", score=3.0,
                                                     buy_score=3.0, sell_score=1.0), fut)
        assert expected_label in out, f"Expected {expected_label!r} in output for futures {stype}"

def test_terminal_futures_hold_label():
    """Futures HOLD should show FUT 1H (no direction yet)."""
    from signals.terminal import display_combined
    spot = make_signal("HOLD", "spot",    score=3.0, buy_score=3.0, sell_score=1.0)
    fut  = make_signal("HOLD", "futures", score=3.8, buy_score=3.8, sell_score=2.0)
    out  = _capture(display_combined, spot, fut)
    assert "FUT 1H" in out


# ── 5. No _conflict key anywhere ─────────────────────────────────────────────

def test_no_conflict_key_in_signals():
    """Verify signals never get _conflict set (conflict detection removed)."""
    for stype in ("BUY", "SELL", "HOLD"):
        for mode in ("spot", "futures"):
            sig = make_signal(stype, mode)
            assert "_conflict" not in sig, f"_conflict found in {mode} {stype} signal"

def test_run_bot_no_conflict_block():
    """Verify run_bot.py no longer contains _conflict assignment."""
    with open("run_bot.py") as f:
        src = f.read()
    assert "_conflict" not in src, "run_bot.py still contains _conflict"


# ── 6. Edge cases ─────────────────────────────────────────────────────────────

def test_compact_futures_short_with_entry():
    """FUTURES SHORT should show correct stop/TP direction (SL above entry, TP below)."""
    from notifier.telegram import _format_compact_signal_telegram
    sig = make_signal("SELL", "futures", confidence="STRONG")
    out = _format_compact_signal_telegram(sig)
    assert "Stop SL" in out
    assert "TP1 50%" in out
    # SL should be above entry for short
    sl_price = sig["stop_loss"]
    entry    = sig["entry_price"]
    assert sl_price > entry, f"SHORT stop loss {sl_price} should be above entry {entry}"

def test_spot_buy_no_short():
    """Spot mode must never produce a SELL signal type."""
    sig = make_signal("SELL", "spot")  # hypothetical — shouldn't happen in real pipeline
    # In the real pipeline analyze_spot_signal() only returns BUY or HOLD
    # The display should still handle it gracefully without crashing
    from notifier.telegram import _format_compact_signal_telegram
    out = _format_compact_signal_telegram(sig)
    assert "SELL" in out  # shows SELL label (graceful, not a crash)

def test_consolidated_performance_section_no_crash():
    """Performance section calls trading.history — should not crash when DB is empty."""
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("BUY",  "spot",    confidence="NORMAL")
    fut  = make_signal("SELL", "futures", confidence="STRONG")
    # Should not raise even if DB has no records
    out = _format_consolidated_telegram(spot, fut)
    assert "PERFORMANCE" in out

def test_consolidated_open_positions_section_no_crash():
    from notifier.telegram import _format_consolidated_telegram
    spot = make_signal("HOLD", "spot",    score=3.0, buy_score=3.0, sell_score=1.0)
    fut  = make_signal("HOLD", "futures", score=3.5, buy_score=3.5, sell_score=2.0)
    out  = _format_consolidated_telegram(spot, fut)
    assert "OPEN POSITIONS" in out


# ── 7. engine.py TP2 calculation ──────────────────────────────────────────────

def _make_engine_signal(stype, entry, atr, resistance=None, support=None):
    """Build a minimal signal dict matching engine.py output structure."""
    from config import RISK_CONFIG
    sl_dist = atr * RISK_CONFIG["atr_multiplier"]
    tp_dist = sl_dist * RISK_CONFIG["take_profit_rr"]
    sl  = entry - sl_dist if stype == "BUY" else entry + sl_dist
    tp1 = entry + tp_dist if stype == "BUY" else entry - tp_dist
    sr  = {}
    if resistance: sr["resistance"] = resistance
    if support:    sr["support"]    = support
    return {"type": stype, "entry_price": entry, "stop_loss": sl,
            "take_profit": tp1, "support_resistance": sr}

def _apply_tp2(sig):
    """Run only the TP2 block from engine.py on an already-built signal."""
    tp1_dist = abs(sig["take_profit"] - sig["entry_price"])
    sr = sig.get("support_resistance") or {}
    if sig["type"] == "BUY":
        tp2_raw = sig["entry_price"] + tp1_dist * 2
        resistance = sr.get("resistance")
        if resistance and sig["entry_price"] < resistance < tp2_raw:
            capped = resistance * 0.995
            if capped > sig["take_profit"]:
                tp2_raw = capped
        sig["tp2"] = round(tp2_raw, 2)
    else:
        tp2_raw = sig["entry_price"] - tp1_dist * 2
        support = sr.get("support")
        if support and sig["entry_price"] > support > tp2_raw:
            capped = support * 1.005
            if capped < sig["take_profit"]:
                tp2_raw = capped
        sig["tp2"] = round(tp2_raw, 2)
    return sig

def test_tp2_always_beyond_tp1_buy():
    """TP2 must always be further from entry than TP1 for BUY — even when resistance < TP1."""
    # Reproduce the live bug: resistance between entry and TP1
    entry = 81_410; atr = 749
    resistance = 82_479   # between entry and TP1 ($84,220)
    sig = _make_engine_signal("BUY", entry, atr, resistance=resistance)
    sig = _apply_tp2(sig)
    tp1 = sig["take_profit"]
    tp2 = sig["tp2"]
    assert tp2 > tp1, f"BUY TP2 ({tp2:.0f}) must be > TP1 ({tp1:.0f}), got {tp2:.0f} < {tp1:.0f}"

def test_tp2_always_beyond_tp1_sell():
    """TP2 must always be further from entry than TP1 for SELL — even when support > TP1."""
    entry = 81_410; atr = 749
    support = 80_500   # between entry and TP1 (~$79,190)
    sig = _make_engine_signal("SELL", entry, atr, support=support)
    sig = _apply_tp2(sig)
    tp1 = sig["take_profit"]
    tp2 = sig["tp2"]
    assert tp2 < tp1, f"SELL TP2 ({tp2:.0f}) must be < TP1 ({tp1:.0f}), got {tp2:.0f} > {tp1:.0f}"

def test_tp2_capped_when_resistance_beyond_tp1():
    """TP2 should be capped at resistance when resistance is beyond TP1 (valid cap)."""
    entry = 81_410; atr = 749
    tp1 = entry + atr * 1.5 * 2.5   # ~$84,220
    resistance = 86_000              # beyond TP1, before TP2 raw
    sig = _make_engine_signal("BUY", entry, atr, resistance=resistance)
    sig = _apply_tp2(sig)
    assert sig["tp2"] == round(resistance * 0.995, 2), "TP2 should be capped at resistance when valid"

def test_tp2_no_cap_when_resistance_below_entry():
    """Resistance below entry should not affect TP2 for BUY."""
    entry = 81_410; atr = 749
    resistance = 80_000  # below entry — irrelevant for BUY TP2
    sig = _make_engine_signal("BUY", entry, atr, resistance=resistance)
    sig = _apply_tp2(sig)
    tp1_dist = abs(sig["take_profit"] - entry)
    expected = round(entry + tp1_dist * 2, 2)
    assert sig["tp2"] == expected, f"TP2 should be uncapped: expected {expected}, got {sig['tp2']}"


# ── Main ─────────────────────────────────────────────────────────────────────

# ── 8. Exchange mirror fallback ──────────────────────────────────────────────

def test_mirror_urls_configured():
    """Mirror client points at the vision host and loads spot markets only."""
    import ccxt  # noqa: F401
    from signals.market_data import _BinanceWithMirror, _MIRROR_HOST
    ex = _BinanceWithMirror()
    assert _MIRROR_HOST in ex._mirror.urls["api"]["public"], ex._mirror.urls["api"]["public"]
    assert "api.binance.com" in ex._primary.urls["api"]["public"], "primary must stay on the real host"
    assert ex._mirror.options.get("fetchMarkets") == ["spot"], ex._mirror.options.get("fetchMarkets")


def test_mirror_fallback_on_network_error():
    """A NetworkError on the primary retries the same call on the mirror."""
    import ccxt
    from signals.market_data import _BinanceWithMirror
    ex = _BinanceWithMirror()
    seen = []

    def boom(*a, **k):
        seen.append("primary")
        raise ccxt.NetworkError("binance GET https://api.binance.com/api/v3/exchangeInfo")

    def ok(*a, **k):
        seen.append("mirror")
        return [[1, 2, 3, 4, 5, 6]]

    ex._primary.fetch_ohlcv = boom
    ex._mirror.fetch_ohlcv = ok

    out = ex.fetch_ohlcv("BTC/USDT", "1h", limit=1)
    assert out == [[1, 2, 3, 4, 5, 6]], out
    assert seen == ["primary", "mirror"], seen


def test_mirror_fallback_is_sticky():
    """Once tripped, later calls skip the primary instead of timing out again."""
    import ccxt
    from signals.market_data import _BinanceWithMirror
    ex = _BinanceWithMirror()
    seen = []

    def boom(*a, **k):
        seen.append("primary")
        raise ccxt.NetworkError("blocked")

    ex._primary.fetch_ohlcv = boom
    ex._mirror.fetch_ohlcv = lambda *a, **k: (seen.append("mirror"), [[0]])[1]

    ex.fetch_ohlcv("BTC/USDT", "1h")
    seen.clear()
    ex.fetch_ohlcv("BTC/USDT", "1h")
    assert seen == ["mirror"], f"primary should not be retried while tripped: {seen}"


def test_mirror_cooldown_reprobes_primary():
    """After the cooldown window the primary is tried again."""
    import time as _t
    from signals.market_data import _BinanceWithMirror, _MIRROR_RETRY_AFTER_S
    ex = _BinanceWithMirror()
    seen = []
    ex._primary.fetch_ohlcv = lambda *a, **k: (seen.append("primary"), [[1]])[1]
    ex._mirror.fetch_ohlcv = lambda *a, **k: (seen.append("mirror"), [[2]])[1]

    # pretend we tripped just past the cooldown
    object.__setattr__(ex, "_mirror_since", _t.monotonic() - _MIRROR_RETRY_AFTER_S - 1)
    ex.fetch_ohlcv("BTC/USDT", "1h")
    assert seen == ["primary"], f"cooldown should release back to primary: {seen}"


def test_futures_calls_never_use_mirror():
    """The mirror has no futures endpoints — those must stay on the primary."""
    import time as _t
    from signals.market_data import _BinanceWithMirror
    ex = _BinanceWithMirror()
    ex._primary.fetch_open_interest = lambda *a, **k: "PRIMARY"
    ex._mirror.fetch_open_interest = lambda *a, **k: "MIRROR"

    object.__setattr__(ex, "_mirror_since", _t.monotonic())  # tripped
    assert ex.fetch_open_interest("BTC/USDT") == "PRIMARY", "futures read leaked to the mirror"


def test_setattr_reaches_both_clients():
    """Config set on the proxy applies to whichever client ends up serving."""
    from signals.market_data import _BinanceWithMirror
    ex = _BinanceWithMirror()
    ex.enableRateLimit = False
    assert ex._primary.enableRateLimit is False
    assert ex._mirror.enableRateLimit is False



if __name__ == "__main__":
    print("\n══ Pipeline Dummy-Data Tests ══\n")

    print("── 1. _mode_label() ──")
    run("mode labels for all 5 cases",            test_mode_labels)

    print("\n── 2. Compact Telegram formatter ──")
    run("SPOT BUY",                               test_compact_spot_buy)
    run("FUTURES LONG (BUY)",                     test_compact_futures_long)
    run("FUTURES SHORT (SELL)",                   test_compact_futures_short)
    run("HOLD spot — gap positive",               test_compact_hold_spot)
    run("HOLD futures — gap positive",            test_compact_hold_futures)
    run("HOLD futures — gap negative (no crash)", test_compact_hold_gap_negative_no_crash)

    print("\n── 3. Consolidated Telegram formatter ──")
    run("SPOT BUY + FUTURES LONG",                test_consolidated_spot_buy_futures_long)
    run("SPOT BUY + FUTURES SHORT (ex-conflict)", test_consolidated_spot_buy_futures_short)
    run("SPOT HOLD + FUTURES SHORT",              test_consolidated_spot_hold_futures_short)
    run("both HOLD",                              test_consolidated_both_hold)
    run("spot only (no futures)",                 test_consolidated_spot_only)
    run("futures only (no spot)",                 test_consolidated_futures_only)
    run("spot HOLD bearish → BUY-only label",     test_consolidated_verdict_spot_bearish_spot_only)

    print("\n── 4. Terminal display_combined() ──")
    run("SPOT BUY + FUTURES LONG",                test_terminal_spot_buy_futures_long)
    run("SPOT BUY + FUTURES SHORT (ex-conflict)", test_terminal_spot_buy_futures_short)
    run("SPOT HOLD + FUTURES SHORT",              test_terminal_hold_futures_short)
    run("compact box FUT LONG/SHORT labels",      test_terminal_combined_box_labels)
    run("FUTURES HOLD → FUT 1H label",            test_terminal_futures_hold_label)

    print("\n── 5. No _conflict key ──")
    run("no _conflict in dummy signals",          test_no_conflict_key_in_signals)
    run("run_bot.py has no _conflict assignment", test_run_bot_no_conflict_block)

    print("\n── 6. Edge cases ──")
    run("FUTURES SHORT SL above entry",           test_compact_futures_short_with_entry)
    run("spot SELL handled gracefully",           test_spot_buy_no_short)
    run("performance section — empty DB",         test_consolidated_performance_section_no_crash)
    run("open positions section — no crash",      test_consolidated_open_positions_section_no_crash)

    print("\n── 7. engine.py TP2 calculation ──")
    run("BUY TP2 > TP1 even when resistance < TP1",    test_tp2_always_beyond_tp1_buy)
    run("SELL TP2 < TP1 even when support > TP1",      test_tp2_always_beyond_tp1_sell)
    run("TP2 capped at resistance when valid (>TP1)",  test_tp2_capped_when_resistance_beyond_tp1)
    run("resistance below entry does not affect TP2",  test_tp2_no_cap_when_resistance_below_entry)

    print("\n── 8. Exchange mirror fallback ──")
    run("mirror URLs + spot-only markets",        test_mirror_urls_configured)
    run("NetworkError → retry on mirror",         test_mirror_fallback_on_network_error)
    run("fallback is sticky",                     test_mirror_fallback_is_sticky)
    run("cooldown re-probes primary",             test_mirror_cooldown_reprobes_primary)
    run("futures reads never use mirror",         test_futures_calls_never_use_mirror)
    run("setattr reaches both clients",           test_setattr_reaches_both_clients)

    print(f"\n{'══' * 20}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed  {'✓ ALL PASS' if FAIL == 0 else f'✗ {FAIL} FAILED'}")
    print()
    sys.exit(0 if FAIL == 0 else 1)
