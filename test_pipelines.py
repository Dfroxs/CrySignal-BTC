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



# ── 9. Phase 2 / Phase 4 error reporting ─────────────────────────────────────

def test_pipeline_reraises_instead_of_returning_none():
    """A failing pipeline must surface its real exception, not return None.

    Returning None is what produced the misleading
    "'NoneType' object is not subscriptable" in run_bot.py Phase 2.
    """
    import ccxt
    import signals.spot as sp
    import signals.futures as fu

    for mod, name in ((sp, "analyze_spot_signal"), (fu, "analyze_futures_signal")):
        original = mod.fetch_ohlcv_df
        cache = getattr(mod, "_spot_cache", None) or getattr(mod, "_futures_cache", None)
        saved = dict(cache) if cache else None
        if cache:
            cache["timestamp"] = 0
            cache["signal"] = None
        mod.fetch_ohlcv_df = lambda *a, **k: (_ for _ in ()).throw(
            ccxt.NetworkError("binance GET https://api.binance.com/api/v3/exchangeInfo")
        )
        try:
            raised = None
            try:
                getattr(mod, name)(symbol="BTC/USDT", include_news=False)
            except Exception as e:
                raised = e
            assert raised is not None, f"{name} swallowed the error and returned instead"
            assert isinstance(raised, ccxt.NetworkError), f"{name} masked the cause: {type(raised).__name__}"
        finally:
            mod.fetch_ohlcv_df = original
            if cache and saved:
                cache.update(saved)


def test_send_signal_alert_returns_zero_when_no_signals():
    """Both signals None → nothing sent, and the count says so."""
    from notifier.common import send_signal_alert
    assert send_signal_alert(spot_signal=None, futures_signal=None) == 0


def test_send_signal_alert_counts_delivered():
    """The count reflects what the transport actually delivered."""
    import notifier.common as nc
    import notifier.telegram as nt

    sent_ok = nc._send_telegram_message
    combined = nt._send_combined_telegram
    try:
        nt._send_combined_telegram = lambda s, f, sym: 2
        assert nc.send_signal_alert(spot_signal={"mode": "spot"}, futures_signal={"mode": "futures"}) == 2

        nt._send_combined_telegram = lambda s, f, sym: 0      # transport refused
        assert nc.send_signal_alert(spot_signal={"mode": "spot"}, futures_signal={"mode": "futures"}) == 0
    finally:
        nc._send_telegram_message = sent_ok
        nt._send_combined_telegram = combined


def test_combined_telegram_returns_zero_without_credentials():
    """No token/chat configured → 0 delivered, never a bare None."""
    import notifier.telegram as nt
    tok, chat = nt.TELEGRAM_BOT_TOKEN, nt.TELEGRAM_CHAT_ID
    try:
        nt.TELEGRAM_BOT_TOKEN = ""
        nt.TELEGRAM_CHAT_ID = ""
        assert nt._send_combined_telegram({"mode": "spot"}, {"mode": "futures"}, "BTC/USDT") == 0
    finally:
        nt.TELEGRAM_BOT_TOKEN, nt.TELEGRAM_CHAT_ID = tok, chat



# ── 10. Threshold & confidence consistency ───────────────────────────────────

def _frame(close, end="2026-01-14 14:00"):
    """Wrap a close series into an OHLCV frame carrying every indicator column
    generate_signals() needs. The index ends at 14:00 UTC so the session bump is
    the US −0.25. No network, no DB."""
    import numpy as np
    import pandas as pd
    from signals.indicators import (calculate_atr, calculate_bollinger_bands,
                                    calculate_ema, calculate_macd, calculate_obv,
                                    calculate_rsi, calculate_stoch_rsi,
                                    calculate_vwap, compute_cmf, compute_mfi)
    rng = np.random.default_rng(11)
    n   = len(close)
    df = pd.DataFrame({
        "open":   close + rng.normal(0, 15, n),
        "high":   close + np.abs(rng.normal(70, 18, n)),
        "low":    close - np.abs(rng.normal(70, 18, n)),
        "close":  close,
        "volume": np.abs(rng.normal(1000, 90, n)),
    }, index=pd.date_range(end=end, periods=n, freq="1h"))
    df['EMA_200'] = calculate_ema(df['close'], 200)
    df['RSI_14']  = calculate_rsi(df['close'])
    df['MACD'], df['MACD_Signal'], df['MACD_Histogram'] = calculate_macd(df['close'])
    df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df['close'])
    df['ATR_14']  = calculate_atr(df)
    df['OBV']     = calculate_obv(df)
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stoch_rsi(df['close'])
    df['VWAP_24'] = calculate_vwap(df, period=24)
    df['MFI_14']  = compute_mfi(df)
    df['CMF_20']  = compute_cmf(df)
    return df

def _synthetic_df(n=320):
    """Seeded uptrend → deterministic TRENDING regime (bump −0.25)."""
    import numpy as np
    rng = np.random.default_rng(7)
    return _frame(80_000 + np.arange(n) * 25 + rng.normal(0, 60, n))

def _selloff_df(n=320, drop=14):
    """Uptrend ending in a sharp flush → RSI and MFI both at an oversold extreme
    while BB-lower and the StochRSI crossover stay quiet, so the correlated-extreme
    cluster holds exactly two members."""
    import numpy as np
    rng   = np.random.default_rng(3)
    close = 80_000 + np.arange(n) * 20 + rng.normal(0, 50, n)
    close[-drop:] = close[-drop - 1] - np.cumsum(np.abs(rng.normal(320, 40, drop)))
    return _frame(close)

def _engine_threshold(mode, override):
    from signals.engine import generate_signals
    sig = generate_signals(_synthetic_df(), htf=None, market_structure=None,
                           sr=None, mode=mode, threshold_override=override)
    assert sig["_regime"]["threshold_bump"] < 0, \
        "fixture must produce a negative regime bump for these tests to mean anything"
    return sig

_SESSION_BUMP = -0.25   # the fixture's last candle is 14:00 UTC — US session

def test_session_and_regime_bumps_apply_in_full():
    """The bumps must move the effective bar even when the adaptive base already
    sits at its mode minimum — that is exactly when the controller wants a lower
    bar. Re-applying the per-mode floor here clipped them to nothing, leaving
    only the +0.5 Asia bump and making the mechanism one-directional."""
    from config import THRESHOLD_MIN
    sig = _engine_threshold("futures", THRESHOLD_MIN)
    expected = THRESHOLD_MIN + sig["_regime"]["threshold_bump"] + _SESSION_BUMP
    assert abs(sig["_threshold"] - expected) < 0.011, \
        f"bumps were clipped: {sig['_threshold']} != {expected}"
    assert sig["_threshold"] < THRESHOLD_MIN, \
        "a negative bump must be able to take the effective bar below the base floor"

def test_spot_bumps_apply_in_full_too():
    from config import SPOT_THRESHOLD_MIN
    sig = _engine_threshold("spot", SPOT_THRESHOLD_MIN)
    expected = SPOT_THRESHOLD_MIN + sig["_regime"]["threshold_bump"] + _SESSION_BUMP
    assert abs(sig["_threshold"] - expected) < 0.011, \
        f"bumps were clipped: {sig['_threshold']} != {expected}"

def test_absolute_sanity_floor_holds():
    """A pathological override must not yield a zero or negative bar — below the
    sanity floor a threshold stops being a bar and becomes an off switch."""
    from signals.engine import _ABS_MIN_THRESHOLD, generate_signals
    sig = generate_signals(_synthetic_df(), htf=None, market_structure=None,
                           sr=None, mode="futures", threshold_override=0.1)
    assert sig["_threshold"] == _ABS_MIN_THRESHOLD, \
        f"expected the sanity floor {_ABS_MIN_THRESHOLD}, got {sig['_threshold']}"

def test_engine_does_not_reapply_the_mode_minimum():
    """The per-mode floor belongs to the adaptive controller, which ends with
    `max(base - step, t_min)`. Enforcing it in both places is what made the
    bumps inert."""
    import ast
    with open("signals/engine.py") as f:
        tree = ast.parse(f.read())
    # AST, not text search: the comment explaining why the floor was removed
    # names the constant, and a substring check would trip on its own rationale.
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {a.name for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom) for a in n.names}
    leaked = referenced & {"THRESHOLD_MIN", "SPOT_THRESHOLD_MIN"}
    assert not leaked, \
        f"engine must not re-floor at the mode minimum — market_data already does: {leaked}"

def test_news_overlay_preserves_htf_confidence_downgrade():
    """STRONG requires 1D HTF agreement (audit #9). The post-news recalculation
    must not promote a downgraded signal back to STRONG — that would unlock spot
    pyramiding (min_confidence = STRONG) on an unconfirmed setup."""
    import signals.engine as eng
    orig = eng.check_upcoming_macro_events
    eng.check_upcoming_macro_events = lambda: (False, None)   # no macro window
    try:
        news = {"fear_greed": {"value": 50, "label": "Neutral"},
                "sentiment": "NEUTRAL", "confidence": 0}
        def _sig():   # strength 7.0 vs threshold 4.3 → STRONG zone on score alone
            return {"type": "BUY", "strength": 7.0, "_threshold": 4.3,
                    "reasons": [], "confidence": "NORMAL"}
        agrees = eng.integrate_news_with_signal(_sig(), news, {"1d": "BULLISH"})
        assert agrees["confidence"] == "STRONG", "1D agreement should stay STRONG"
        unknown = eng.integrate_news_with_signal(_sig(), news, {"1d": "NEUTRAL"})
        assert unknown["confidence"] == "NORMAL", \
            f"1D not confirming must downgrade to NORMAL, got {unknown['confidence']}"
    finally:
        eng.check_upcoming_macro_events = orig

def test_pipelines_keep_effective_threshold():
    """spot.py / futures.py must not overwrite the engine's effective _threshold
    with the raw adaptive base — sizing and re-entry checks read this key."""
    for path in ("signals/spot.py", "signals/futures.py"):
        with open(path) as f:
            src = f.read()
        assert "signal['_threshold'] = threshold" not in src, \
            f"{path} overwrites the effective threshold set by generate_signals()"


# ── 11. Correlated-extreme cluster & spot cache hygiene ──────────────────────

def _divergence_flush_df(n=320):
    """Higher high printed on weaker RSI (bearish divergence), then a monotonic
    flush into oversold so RSI ≤30 and MFI ≤20 both fire. The divergence then
    cancels the RSI leg, leaving a single member in the correlated cluster."""
    import numpy as np
    rng = np.random.default_rng(5)
    c = np.empty(n)
    c[:263]    = 80_000 + np.arange(263) * 10 + rng.normal(0, 25, 263)
    c[263:281] = c[262] + np.cumsum(np.full(18, 300.0))       # steep rally → peak A, high RSI
    c[281:293] = c[280] - np.cumsum(np.full(12, 210.0))       # pullback
    c[293:307] = c[292] + np.cumsum(np.full(14, 230.0))       # slow grind → higher high B, weaker RSI
    c[307:]    = c[306] - np.cumsum(np.full(n - 307, 430.0))  # monotonic flush → oversold
    return _frame(c)

def test_mfi_extreme_counts_in_correlated_cluster():
    """MFI ≤20 reads the same price extreme as RSI ≤30 — it must be discounted by
    the diminishing-returns block, not stack a full +1.5 on top of it."""
    from signals.engine import generate_signals
    df  = _selloff_df()
    last = df.iloc[-1]
    assert last['RSI_14'] <= 30 and last['MFI_14'] <= 20, "fixture must hit both extremes"
    assert last['close'] > last['BB_Lower'], "fixture must leave BB out of the cluster"

    sig = generate_signals(df, htf=None, market_structure=None, sr=None,
                           mode="spot", threshold_override=4.3)
    clustered = [r for r in sig['reasons'] if 'conditions clustered' in r]
    assert clustered, "RSI + MFI oversold must register as a cluster"
    assert "-0.75" in clustered[0], f"expected a 2-member penalty, got: {clustered[0]}"

def test_cancelled_rsi_extreme_leaves_the_cluster():
    """When a divergence cancels the RSI OS/OB score, that extreme must drop out
    of the correlated cluster — otherwise the side is penalised for a component
    that is no longer contributing anything."""
    from signals.engine import generate_signals
    df   = _divergence_flush_df()
    last = df.iloc[-1]
    assert last['RSI_14'] <= 30 and last['MFI_14'] <= 20, "fixture must hit both extremes"

    sig = generate_signals(df, htf=None, market_structure=None, sr=None,
                           mode="spot", threshold_override=4.3)
    assert sig['rsi_divergence'] == 'BEARISH', f"fixture lost its divergence: {sig['rsi_divergence']}"
    assert [r for r in sig['reasons'] if 'cancelled by BEARISH divergence' in r], \
        "fixture must exercise the divergence-cancel branch"
    clustered = [r for r in sig['reasons'] if 'conditions clustered' in r]
    assert not clustered, f"cancelled RSI still counted as a clustered extreme: {clustered}"


def test_spot_cache_hit_is_flagged_stale():
    """A replayed 4H analysis must be marked so Phase 3 refuses to open on it,
    and the stored copy must stay unflagged for the next replay."""
    import time as _time
    import signals.spot as sp
    saved = dict(sp._spot_cache)
    try:
        sp._spot_cache["timestamp"] = int(_time.time() // (4 * 3600))
        sp._spot_cache["signal"] = {"type": "BUY", "entry_price": 80_000.0, "_cached": False}
        out = sp.analyze_spot_signal()          # cache hit — returns before any I/O
        assert out["_cached"] is True, "cached signal must be flagged stale"
        assert sp._spot_cache["signal"]["_cached"] is False, "stored copy must stay unflagged"
    finally:
        sp._spot_cache.update(saved)

def test_spot_cache_stores_a_copy_not_the_returned_object():
    """On a cache MISS the computed signal is both returned and stored. run_bot
    then mutates what it was given — the circuit breaker forces type=HOLD, a
    pyramid entry rewrites stop_loss/take_profit/tp2 — so storing the same
    object made those Phase 3 edits the base signal replayed for the rest of the
    4H candle. The read path was already deep-copied; the store path was not."""
    with open("signals/spot.py") as f:
        src = f.read()
    assert '_spot_cache["signal"] = copy.deepcopy(signal)' in src, \
        "the cache store must deep-copy — run_bot mutates the object it is handed"
    assert '_spot_cache["signal"] = signal\n' not in src, \
        "a bare reference store has come back"

def test_run_bot_refuses_entry_on_cached_spot_signal():
    """Phase 3 must consult the flag and record the skip as a gate block."""
    with open("run_bot.py") as f:
        src = f.read()
    assert 'spot_signal.get("_cached")' in src, "run_bot.py ignores the stale-cache flag"
    assert '"stale_cache"' in src, "the skip must be logged to signal_blocks"


# ── 12. Backtest fidelity & risk accounting ──────────────────────────────────

def _gate_window(n=60, spike_at=-15):
    """Flat range with one tall spike 15 bars back — inside a 24-bar window,
    outside a 6-bar one."""
    import numpy as np
    import pandas as pd
    close = np.full(n, 80_000.0)
    high, low = close + 100, close - 100
    high[spike_at] = 84_000.0
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                       "volume": np.full(n, 1000.0)})
    df["EMA_200"], df["VWAP_24"], df["ATR_14"] = 79_000.0, 79_900.0, 300.0
    return df

def _gate_signal():
    return {"type": "BUY", "confidence": "NORMAL", "strength": 6.0, "_threshold": 5.2,
            "entry_price": 80_000.0, "stop_loss": 79_400.0, "take_profit": 81_500.0,
            "support_resistance": {},
            "_regime": {"regime": "TRENDING", "trend_dir": "BULLISH"}}

def test_wick_gate_measures_24_hours_in_both_modes():
    """The fakeout gate looks back 24 HOURS: 6 bars on spot 4H, 24 on futures 1H.
    They were swapped, so spot measured 96h and futures 6h."""
    from backtest import _failing_gates
    w = _gate_window()
    assert _failing_gates(_gate_signal(), "spot", w) == [], \
        "spot must look at 6 bars (4H × 6 = 24h) — the spike is outside it"
    assert "fakeout_first" in _failing_gates(_gate_signal(), "futures", w), \
        "futures must look at 24 bars (1H × 24 = 24h) — the spike is inside it"

def test_failing_gates_reports_every_gate_not_just_the_first():
    """Attribution needs all of them: with an early return the gate checked
    first absorbs the credit and everything behind it looks inert."""
    from backtest import _failing_gates
    w = _gate_window()
    bad = _gate_signal()
    bad["confidence"] = "WEAK"                       # trips confidence_first
    bad["_regime"] = {"regime": "TRENDING", "trend_dir": "BEARISH"}   # + regime_counter
    gates = _failing_gates(bad, "futures", w)
    assert "confidence_first" in gates and "regime_counter" in gates, gates
    assert len(gates) >= 3, f"confluence should fail too, got {gates}"
    assert len(gates) == len(set(gates)), f"a gate must not be counted twice: {gates}"


def test_backtest_cost_model_matches_live():
    """backtest._net_pnl must agree with trading/paper.py::_calc_pnl — the old
    harness charged TP2 once and trailing exits twice."""
    from backtest import _net_pnl
    from trading.paper import _calc_pnl
    entry = 80_000.0
    for mode in ("spot", "futures"):
        for stype, exit_px in (("BUY", 81_500.0), ("BUY", 79_200.0),
                               ("SELL", 78_500.0), ("SELL", 80_900.0)):
            for partial, ppnl in ((0, 0.0), (1, 1.85)):
                pos = {"entry_price": entry, "type": stype, "mode": mode,
                       "partial_closed": partial, "partial_pnl": ppnl}
                live = _calc_pnl(pos, exit_px)
                bt   = _net_pnl(stype, entry, exit_px, bool(partial), ppnl, mode)
                assert abs(live - bt) < 1e-9, \
                    f"{mode} {stype} partial={partial}: live {live:.6f} vs backtest {bt:.6f}"

def test_drawdown_is_peak_to_trough():
    """An account up 30% that gives back 18% is in an 18% drawdown even though
    cumulative P&L is still positive — the old breaker read max(0, -total) = 0."""
    import os
    import sqlite3
    import tempfile
    import trading.history as h
    saved_path, saved_db = h.SIGNAL_HISTORY_DB, h.DB
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        h.SIGNAL_HISTORY_DB, h.DB = path, None
        c = h._conn()
        for i, pnl in enumerate([12.0, 18.0, -10.0, -8.0]):     # peak +30, ends +12
            c.execute(
                "INSERT INTO paper_positions "
                "(type,entry_price,stop_loss,take_profit,opened_at,closed_at,outcome,pnl_pct,mode) "
                "VALUES ('BUY',80000,79000,82000,?,?,?,?,'spot')",
                (f"2026-08-2{i}T00:00:00", f"2026-08-2{i}T06:00:00",
                 "WIN" if pnl > 0 else "LOSS", pnl),
            )
        c.commit()
        total, _, _ = h.get_closed_pnl("spot")
        cur_dd, max_dd = h.get_drawdown("spot")
        assert abs(total - 12.0) < 1e-6, f"cumulative P&L should stay positive, got {total}"
        assert abs(cur_dd - 18.0) < 1e-6, f"current drawdown should be 18%, got {cur_dd}"
        assert abs(max_dd - 18.0) < 1e-6, f"max drawdown should be 18%, got {max_dd}"
        assert max(0, -total) == 0, "the old measure saw no drawdown at all"
    finally:
        try:
            h.DB.close()
        except Exception:
            pass
        h.SIGNAL_HISTORY_DB, h.DB = saved_path, saved_db
        os.unlink(path)

def _htf_frames(spec):
    """Build the (series, close_times) tuples _load_htf_series produces.

    Close times are precomputed there so _htf_at can binary-search instead of
    masking the whole series on every candle; the tests must use the same shape
    or they stop exercising the real lookup.
    """
    import pandas as pd
    out = {}
    for tf, (trends, freq, delta) in spec.items():
        s = _htf_frame(trends, freq)
        out[tf] = (s, (s.index + delta).values)
    return out

def _htf_frame(trends, freq, start="2026-01-01"):
    import pandas as pd
    idx = pd.date_range(start, periods=len(trends), freq=freq)
    n = len(trends)
    return pd.DataFrame({"trend": trends, "rsi": [50.0] * n, "rsi_zone": ["neutral"] * n,
                         "macd": ["BULLISH"] * n, "vol_trend": ["FLAT"] * n,
                         "pct_ema": [1.0] * n}, index=idx)

def test_htf_at_ignores_the_still_forming_bar():
    """Replaying the current (unfinished) HTF bar would leak its remainder
    backwards in time. Only bars that had already closed may be read."""
    import pandas as pd
    from backtest import _htf_at
    frames = _htf_frames({
        "1d": (["BULLISH", "BULLISH", "BEARISH"], "1D", pd.Timedelta(days=1)),
        "1w": (["BULLISH"], "1W", pd.Timedelta(weeks=1)),
    })
    htf = _htf_at(frames, pd.Timestamp("2026-01-03 12:00"))
    assert htf["1d"] == "BULLISH", \
        f"the 01-03 bar had not closed at 12:00 — got {htf['1d']} (look-ahead)"

def test_htf_at_alignment_matches_live_rule():
    """aligned = both timeframes agree, are non-NEUTRAL, and are not both at the
    same RSI extreme — the same rule signals/htf.py applies live."""
    import pandas as pd
    from backtest import _htf_at
    week = pd.Timedelta(weeks=1)
    day  = pd.Timedelta(days=1)
    ts   = pd.Timestamp("2026-03-01")

    agree  = _htf_frames({"1d": (["BULLISH"] * 40, "1D", day),
                          "1w": (["BULLISH"] * 8, "1W", week)})
    differ = _htf_frames({"1d": (["BULLISH"] * 40, "1D", day),
                          "1w": (["BEARISH"] * 8, "1W", week)})
    assert _htf_at(agree, ts)["aligned"] is True
    assert _htf_at(differ, ts)["aligned"] is False
    # An empty frame set means the HTF fetch failed; the run must continue with
    # condition 6 neutral rather than dying.
    assert _htf_at({}, ts) is None

def test_range_fetch_walks_forward_to_the_requested_span():
    """An explicit span is what makes independent replication possible at all —
    paging backwards from now can only ever produce windows that overlap."""
    import signals.ohlcv as oh
    STEP, TOTAL, CAP = 3_600_000, 5_000, 1_000
    hist = [[i * STEP, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(TOTAL)]

    class _CappedExchange:
        calls = 0
        def parse_timeframe(self, tf):
            return STEP // 1000
        def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            _CappedExchange.calls += 1
            n = min(limit or CAP, CAP)
            if since is None:
                return hist[-n:]
            start = next((i for i, b in enumerate(hist) if b[0] >= since), len(hist))
            return hist[start:start + n]

    saved = oh.exchange
    try:
        oh.exchange = _CappedExchange()
        lo, hi = 1_500 * STEP, 4_200 * STEP          # 2700 bars, past the 1000 cap
        bars = oh._fetch_ohlcv_range("BTC/USDT", "1h", lo, hi)
        ts = [b[0] for b in bars]
        assert ts[0] == lo, f"span must start at the requested bar, got {ts[0]}"
        assert ts[-1] == hi, f"span must end at the requested bar, got {ts[-1]}"
        assert len(bars) == 2701, f"expected 2701 candles, got {len(bars)}"
        assert ts == sorted(ts) and len(set(ts)) == len(ts), "pages must not overlap or reorder"
        assert _CappedExchange.calls >= 3, "2700 candles needs more than one capped call"
    finally:
        oh.exchange = saved

def test_range_fetch_stops_at_the_end_of_history():
    """Asking past the end of the exchange's history must terminate, not loop."""
    import signals.ohlcv as oh
    STEP = 3_600_000
    hist = [[i * STEP, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(50)]

    class _ShortExchange:
        def parse_timeframe(self, tf):
            return STEP // 1000
        def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            start = next((i for i, b in enumerate(hist) if b[0] >= (since or 0)), len(hist))
            return hist[start:start + (limit or 1000)]

    saved = oh.exchange
    try:
        oh.exchange = _ShortExchange()
        bars = oh._fetch_ohlcv_range("BTC/USDT", "1h", 0, 9_999 * STEP)
        assert len(bars) == 50, f"should return all it has, got {len(bars)}"
    finally:
        oh.exchange = saved


def test_ohlcv_pagination_beats_the_exchange_cap():
    """A single fetch_ohlcv() silently truncates at 1000 rows, so the futures
    backtest asked for 2360 candles and simulated 42 days calling it 90."""
    import signals.ohlcv as oh
    STEP, TOTAL, CAP = 3_600_000, 5_000, 1_000
    hist = [[i * STEP, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(TOTAL)]

    class _CappedExchange:
        calls = 0
        def parse_timeframe(self, tf):
            return STEP // 1000
        def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            _CappedExchange.calls += 1
            n = min(limit or CAP, CAP)
            if since is None:
                return hist[-n:]
            start = next((i for i, b in enumerate(hist) if b[0] >= since), len(hist))
            return hist[start:start + n]

    saved = oh.exchange
    try:
        oh.exchange = _CappedExchange()
        bars = oh._fetch_ohlcv_paged("BTC/USDT", "1h", 2360)
        ts = [b[0] for b in bars]
        assert len(bars) == 2360, f"expected 2360 candles, got {len(bars)}"
        assert ts == sorted(ts) and len(set(ts)) == len(ts), "pages must not overlap or reorder"
        assert ts[-1] == hist[-1][0], "the newest candle must still be the last row"
        assert _CappedExchange.calls >= 3, "2360 candles needs more than one capped call"
    finally:
        oh.exchange = saved


# ── 13. Walk-forward & cost sensitivity ──────────────────────────────────────

def _wf_trade(entry_time, pnl, outcome="WIN", partial=False):
    return {"entry_time": entry_time, "outcome": outcome, "pnl_pct": pnl,
            "candles_held": 5, "confidence": "NORMAL", "partial": partial,
            "type": "BUY", "strength": 6.0}

def test_walk_forward_windows_span_the_evaluated_period():
    """Windows must cover the period that was TESTED, not the span between the
    first and last trade — a stretch that produced no signal is itself a result."""
    from backtest import walk_forward
    trades = [_wf_trade("2026-06-01 00:00", 1.0), _wf_trade("2026-06-15 00:00", -2.0, "LOSS")]
    wins = walk_forward(trades, 4, start="2026-01-01", end="2026-09-01")
    assert len(wins) == 4
    assert str(wins[0]["from"].date()) == "2026-01-01", "first window must start at the data start"
    assert str(wins[-1]["to"].date()) == "2026-09-01", "last window must end at the data end"
    empty = [w for w in wins if w["n"] == 0]
    assert empty, "windows with no signal must be reported, not dropped"
    assert sum(w["n"] for w in wins) == 2, "every trade must land in exactly one window"

def test_cost_sensitivity_reprices_exactly():
    """Since costs no longer shift entry/stop/target prices, re-pricing at another
    cost level is exact: 2 legs for a plain trade, 1.5 for one that took TP1."""
    from backtest import cost_sensitivity
    from config import EXECUTION_CONFIG as ec
    current = ec["futures_fee_pct"] + ec.get("slippage_pct", 0.05)
    trades = [_wf_trade("2026-06-01 00:00", 1.00, "WIN"),
              _wf_trade("2026-06-02 00:00", -0.50, "LOSS", partial=True)]
    rows = {round(r["per_side"], 4): r for r in cost_sensitivity(trades, "futures", levels=(0.0,))}
    assert round(current, 4) in rows, "the configured cost level must always be shown"
    gross = rows[0.0]["total_pnl"]
    now   = rows[round(current, 4)]["total_pnl"]
    expected = (1.00 + current * 2) + (-0.50 + current * 1.5)
    assert abs(gross - round(expected, 2)) < 0.011, f"gross should be {expected:.3f}, got {gross}"
    assert abs(now - 0.50) < 1e-9, "the current level must reproduce the stored P&L"


# ── 14. Condition attribution & ablation ─────────────────────────────────────

_UNSET = object()

def _contrib_signal(disabled=_UNSET):
    """`disabled` defaults to () — score everything — so a test is not silently
    measuring the configured active set unless it asks for it."""
    from signals.engine import generate_signals
    from signals.indicators import detect_support_resistance
    df = _synthetic_df()
    return generate_signals(df, htf=None, market_structure=None,
                            sr=detect_support_resistance(df), mode="futures",
                            threshold_override=5.2,
                            disabled=() if disabled is _UNSET else disabled)

def test_contributions_sum_to_the_scores():
    """The attribution checkpoints only read the accumulators. If the parts stop
    summing to the whole, a condition is being counted twice or not at all."""
    sig = _contrib_signal()
    contrib = sig["_contributions"]
    assert contrib, "engine must emit per-condition contributions"
    assert abs(sum(b for b, _ in contrib.values()) - sig["buy_score"]) < 0.011
    assert abs(sum(s for _, s in contrib.values()) - sig["sell_score"]) < 0.011

# Conditions scored after the HTF block, which holds the last code that reads the
# running totals (its conflict penalty picks whichever side is ahead). Ablating
# one of these changes the totals and nothing else, so the arithmetic is exact.
_LATE_CONDITIONS = ["taker", "cmf", "candlestick", "extreme_cluster_penalty",
                    "rsi_divergence", "mfi", "oi_price", "gold_vix", "adx",
                    "vwap", "support_resistance", "stoch_rsi", "obv"]

def test_ablation_removes_exactly_that_condition():
    """Disabling a condition must subtract its contribution and nothing else."""
    base = _contrib_signal()
    contrib = base["_contributions"]
    target = next((n for n in _LATE_CONDITIONS
                   if contrib.get(n, (0.0, 0.0)) != (0.0, 0.0)), None)
    assert target, f"fixture scores none of the late conditions: {contrib}"

    off = _contrib_signal(disabled=[target])
    b_buy, b_sell = contrib[target]
    assert off["_contributions"][target] == (0.0, 0.0), \
        f"{target} was ablated but still contributed"
    assert abs(off["buy_score"] - (base["buy_score"] - b_buy)) < 0.011, \
        f"{target}: buy {off['buy_score']} != {base['buy_score']} - {b_buy}"
    assert abs(off["sell_score"] - (base["sell_score"] - b_sell)) < 0.011, \
        f"{target}: sell {off['sell_score']} != {base['sell_score']} - {b_sell}"

def test_empty_disable_scores_every_condition():
    """An empty collection means score everything — distinct from None."""
    base, same = _contrib_signal(), _contrib_signal(disabled=[])
    assert (base["buy_score"], base["sell_score"], base["type"]) == \
           (same["buy_score"], same["sell_score"], same["type"])

def test_default_active_set_comes_from_config():
    """`disabled=None` must apply config.DISABLED_CONDITIONS and nothing else —
    the pruned set has to be a config decision, not a caller's."""
    from config import DISABLED_CONDITIONS
    every  = _contrib_signal(disabled=())
    active = _contrib_signal(disabled=None)

    for name in DISABLED_CONDITIONS:
        assert active["_contributions"].get(name, (0.0, 0.0)) == (0.0, 0.0), \
            f"{name} is in DISABLED_CONDITIONS but still scored"

    # Exactness holds as long as no ablated condition also clears a flag a later
    # block reads — rsi_divergence is the one that can, so guard on it.
    if every["_contributions"].get("rsi_divergence", (0.0, 0.0)) == (0.0, 0.0):
        removed_buy = sum(every["_contributions"].get(n, (0.0, 0.0))[0]
                          for n in DISABLED_CONDITIONS)
        assert abs(active["buy_score"] - (every["buy_score"] - removed_buy)) < 0.011, \
            f"active {active['buy_score']} != {every['buy_score']} - {removed_buy}"


def test_thresholds_track_the_active_condition_set():
    """Thresholds are a fraction of the achievable ceiling, so pruning a
    condition lowers the bar with it. An absolute bar would silently become a
    stricter one and any pruning experiment would be measuring two changes."""
    import importlib
    import config
    saved = config.DISABLED_CONDITIONS
    try:
        assert config.SPOT_MAX_SCORE == 22.50 and config.SIGNAL_MAX_SCORE == 26.50, \
            "CONDITION_MAX must reproduce the documented ceilings"
        assert config.SPOT_THRESHOLD == 4.30 and config.SIGNAL_THRESHOLD == 5.20, \
            "the fractions must reproduce the pre-existing thresholds"

        config.DISABLED_CONDITIONS = frozenset({"htf"})       # a 2.00 block
        assert config._max_score("spot") == 20.50, config._max_score("spot")
        assert config._max_score("futures") == 24.50, config._max_score("futures")
        lean = round(24.50 * config._THR_FRACTION, 2)
        assert lean < 5.20, "a smaller ceiling must lower the bar, not keep it"
    finally:
        config.DISABLED_CONDITIONS = saved

def test_condition_max_covers_every_scored_condition():
    """A condition the engine scores but CONDITION_MAX does not know about would
    silently break the ceiling and every threshold derived from it."""
    from config import CONDITION_MAX
    sig = _contrib_signal(disabled=())
    unknown = sorted(set(sig["_contributions"]) - set(CONDITION_MAX))
    assert not unknown, f"conditions missing from CONDITION_MAX: {unknown}"


# ── 15. analyze.py --db ──────────────────────────────────────────────────────

def test_analyze_db_flag_targets_another_database():
    """The paper run lives on a server and analysis runs on a workstation. --db
    must point the report at a pulled copy — the alternative is overwriting the
    local database, which is the only copy of whatever it holds."""
    import io
    import os
    import sys
    import tempfile
    from contextlib import redirect_stdout

    import analyze
    import trading.history as h

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    saved_db_path, saved_argv = analyze.DB_PATH, sys.argv
    saved_hist, saved_conn = h.SIGNAL_HISTORY_DB, h.DB
    try:
        # Build the real schema rather than a hand-rolled one, so the test keeps
        # exercising analyze's actual queries as the schema evolves.
        h.SIGNAL_HISTORY_DB, h.DB = path, None
        c = h._conn()
        c.execute("INSERT INTO cycle_log (timestamp, mode, type, price, strength, threshold) "
                  "VALUES ('2026-09-01 00:00:00', 'spot', 'HOLD', 80000, 5.0, 4.3)")
        c.commit()
        h.DB.close()
        h.SIGNAL_HISTORY_DB, h.DB = saved_hist, saved_conn

        sys.argv = ["analyze.py", "--db", path, "--section", "overview"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyze.main()
        out = buf.getvalue()

        assert path in out, "the report must name the database it came from"
        assert "cycles : 1" in out, f"span line missing or wrong:\n{out[:400]}"
        assert analyze.DB_PATH == path, "--db must actually redirect the connection"
    finally:
        analyze.DB_PATH, sys.argv = saved_db_path, saved_argv
        h.SIGNAL_HISTORY_DB, h.DB = saved_hist, saved_conn
        os.unlink(path)

def test_analyze_defaults_to_the_local_database():
    """Omitting --db must not silently point somewhere else."""
    import analyze
    import inspect
    src = inspect.getsource(analyze.main)
    assert 'default=DB_PATH' in src, "--db must default to the module's DB_PATH"


# ── 16. ForexFactory calendar timezone ───────────────────────────────────────

def test_macro_calendar_is_read_as_utc():
    """The feed publishes in UTC. Reading it as Eastern put every event four
    hours late in summer, so the macro gate stayed open through the actual
    release and then force-closed every position two hours after it had passed.

    Each case below is an event whose release time never moves, so the mapping
    is checkable without the feed: if the feed were Eastern, NFP would print as
    8:30am rather than 12:30pm."""
    from signals.sentiment import _parse_macro_timestamp
    cases = [
        ("09-04-2026 12:30pm", 12, 30, "Non-Farm Payrolls, 08:30 ET"),
        ("09-02-2026 12:15pm", 12, 15, "ADP Non-Farm, 08:15 ET"),
        ("09-01-2026 2:00pm",  14,  0, "ISM Manufacturing PMI, 10:00 ET"),
        ("08-30-2026 11:50pm", 23, 50, "JP Industrial Production, 08:50 JST"),
    ]
    for raw, hour, minute, why in cases:
        dt = _parse_macro_timestamp(raw)
        assert dt.utcoffset().total_seconds() == 0, f"{raw} must be UTC ({why})"
        assert (dt.hour, dt.minute) == (hour, minute), \
            f"{raw} → {dt:%H:%M}, expected {hour:02d}:{minute:02d} ({why})"

def test_feed_timestamps_survive_mixed_rfc2822_spellings():
    """RSS pubDate arrives in several spellings — FinancialJuice ends in `GMT`,
    CoinTelegraph and the rest in `+0000`. `pd.to_datetime(errors='coerce')`
    infers ONE format from the first element and coerces the rest to NaT, so
    which rows survived depended on which scraper happened to be written first.

    The order-independence assertion is the real invariant: on a live CSV the
    old path parsed 3 of 18, and reversing the row order flipped which 3."""
    import pandas as pd
    from signals.sentiment import _parse_feed_timestamps
    gmt = "Sat, 29 Aug 2026 11:14:17 GMT"
    off = "Sat, 29 Aug 2026 12:39:59 +0000"
    for order in ([gmt, off, off], [off, gmt, gmt], [off, gmt, off]):
        out = _parse_feed_timestamps(pd.Series(order))
        assert out.notna().all(), f"order-dependent parse: {order} → {list(out)}"
        assert all(v.tzinfo is not None for v in out), "timestamps must be aware"
        assert all(v.utcoffset().total_seconds() == 0 for v in out), "must land in UTC"

def test_feed_timestamp_parser_survives_junk():
    """A malformed row must become NaT and be filtered, never raise — one bad
    feed entry cannot be allowed to take out the whole sentiment layer."""
    import pandas as pd
    from signals.sentiment import _parse_feed_timestamps
    out = _parse_feed_timestamps(pd.Series(["not a date", "", None,
                                            "Sat, 29 Aug 2026 12:39:59 +0000"]))
    assert out.isna().sum() == 3
    assert out.notna().sum() == 1

def test_macro_parser_does_not_use_a_named_timezone():
    """A regression here is silent — the gate keeps firing, just at the wrong
    hours — so guard the mechanism rather than only the output."""
    import ast
    with open("signals/sentiment.py") as f:
        tree = ast.parse(f.read())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "ZoneInfo" not in names and "zoneinfo" not in names, \
        "the calendar is UTC; converting through a named zone reintroduces the offset"


# ── 17. Silent-failure handlers actually run ─────────────────────────────────

def _assert_degrades_with_a_warning(module, attr, check):
    """Inject a failure into `module.attr` and confirm the handler both survives
    and says something. A NameError inside an except block is invisible until
    the day something else has already gone wrong."""
    import logging
    saved = getattr(module, attr)
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    logging.getLogger(module.__name__).addHandler(handler)
    try:
        setattr(module, attr, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected")))
        check()
    finally:
        setattr(module, attr, saved)
        logging.getLogger(module.__name__).removeHandler(handler)
    assert records, f"{module.__name__}.{attr} failed silently — no warning logged"
    return records

def test_regime_failure_degrades_loudly():
    """UNKNOWN regime is not a neutral default: it zeroes the threshold bump and
    makes run_bot._is_counter_trend_regime() return False, so the counter-trend
    gate stops blocking. That must not happen quietly."""
    import signals.engine as eng
    out = {}
    def run():
        out["sig"] = eng.generate_signals(_synthetic_df(), mode="futures",
                                          threshold_override=5.2)
    msgs = _assert_degrades_with_a_warning(eng, "classify_regime", run)
    assert out["sig"]["regime"] == "UNKNOWN"
    assert any("counter-trend gate inert" in m for m in msgs), msgs

def test_adx_failure_degrades_loudly():
    """A zero ADX halves the MACD crossover weight for the whole cycle."""
    import signals.engine as eng
    def run():
        eng.generate_signals(_synthetic_df(), mode="futures", threshold_override=5.2)
    msgs = _assert_degrades_with_a_warning(eng, "calculate_adx", run)
    assert any("MACD scored at reduced weight" in m for m in msgs), msgs

def test_news_failure_degrades_loudly():
    """If the news CSV cannot be read the signal still scores — without the news
    layer, and previously without a word about it."""
    import signals.sentiment as sent
    def run():
        sent.get_combined_sentiment(fng={"value": 50, "label": "Neutral"})
    msgs = _assert_degrades_with_a_warning(sent, "pd", run)
    assert any("News sentiment unavailable" in m for m in msgs), msgs


# ── 18. Log output is readable in a file ─────────────────────────────────────

def test_interrupt_is_a_clean_stop_not_a_traceback():
    """systemd sends SIGINT and it lands in the loop's time.sleep(), so an
    ordinary `systemctl stop` used to print a traceback. Under Restart=always
    that is one fake traceback per restart, in the file whose whole purpose is
    that a real one stands out."""
    import run_bot
    saved = run_bot.main
    try:
        run_bot.main = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        run_bot._run()          # must return, not raise
    finally:
        run_bot.main = saved

def test_progress_output_is_plain_when_not_a_terminal():
    """Under systemd stdout is a file: escape codes are stored verbatim and
    `\r` overwrites nothing, so progress lines pile onto the line after them —
    'Telegram sent (1 message)ions...'. The run is read from that file for
    weeks."""
    import subprocess
    import sys
    code = (
        "import run_bot;"
        "run_bot._loading('transient');"
        "run_bot._ok('done');"
        "run_bot._err('failed');"
        "run_bot._section('TITLE')"
    )
    # A subprocess with a pipe for stdout is exactly the non-TTY case.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True).stdout
    assert b"\x1b[" not in out, "ANSI escapes must not reach a log file"
    assert b"\r" not in out, "carriage returns overwrite nothing in a file"
    assert b"transient" not in out, "the spinner line has no meaning in a file"
    assert b"done" in out and b"failed" in out, "real output must survive"

def test_colours_return_on_a_terminal():
    """The stripping is conditional, not a removal — an interactive run keeps
    its colour."""
    import run_bot
    import signals.terminal as term
    for mod in (run_bot, term):
        assert hasattr(mod, "_TTY"), f"{mod.__name__} must decide on stdout, not hard-code"
    if run_bot._TTY:
        assert run_bot._G, "colour should be present when attached to a terminal"


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

    print("\n── 9. Phase 2 / Phase 4 error reporting ──")
    run("pipeline re-raises real cause",          test_pipeline_reraises_instead_of_returning_none)
    run("no signals → 0 delivered",               test_send_signal_alert_returns_zero_when_no_signals)
    run("delivered count is honest",              test_send_signal_alert_counts_delivered)
    run("no credentials → 0, not None",           test_combined_telegram_returns_zero_without_credentials)

    print("\n── 10. Threshold & confidence consistency ──")
    run("session/regime bumps apply in full",     test_session_and_regime_bumps_apply_in_full)
    run("spot bumps apply in full",               test_spot_bumps_apply_in_full_too)
    run("absolute sanity floor holds",            test_absolute_sanity_floor_holds)
    run("engine does not re-floor at mode min",   test_engine_does_not_reapply_the_mode_minimum)
    run("news overlay keeps HTF downgrade",       test_news_overlay_preserves_htf_confidence_downgrade)
    run("pipelines keep effective _threshold",    test_pipelines_keep_effective_threshold)

    print("\n── 11. Correlated extremes & spot cache ──")
    run("MFI extreme joins the cluster",          test_mfi_extreme_counts_in_correlated_cluster)
    run("cancelled RSI leaves the cluster",       test_cancelled_rsi_extreme_leaves_the_cluster)
    run("cached spot signal flagged stale",       test_spot_cache_hit_is_flagged_stale)
    run("spot cache stores a copy",               test_spot_cache_stores_a_copy_not_the_returned_object)
    run("run_bot refuses cached spot entry",      test_run_bot_refuses_entry_on_cached_spot_signal)

    print("\n── 12. Backtest fidelity & risk accounting ──")
    run("wick gate = 24h in both modes",          test_wick_gate_measures_24_hours_in_both_modes)
    run("gate attribution lists all gates",       test_failing_gates_reports_every_gate_not_just_the_first)
    run("backtest costs match live",              test_backtest_cost_model_matches_live)
    run("drawdown is peak-to-trough",             test_drawdown_is_peak_to_trough)
    run("HTF ignores the forming bar",            test_htf_at_ignores_the_still_forming_bar)
    run("HTF alignment matches live",             test_htf_at_alignment_matches_live_rule)
    run("OHLCV pagination beats the cap",         test_ohlcv_pagination_beats_the_exchange_cap)
    run("range fetch walks a requested span",     test_range_fetch_walks_forward_to_the_requested_span)
    run("range fetch stops at end of history",    test_range_fetch_stops_at_the_end_of_history)

    print("\n── 13. Walk-forward & cost sensitivity ──")
    run("windows span the evaluated period",      test_walk_forward_windows_span_the_evaluated_period)
    run("cost re-pricing is exact",               test_cost_sensitivity_reprices_exactly)

    print("\n── 14. Condition attribution & ablation ──")
    run("contributions sum to the scores",        test_contributions_sum_to_the_scores)
    run("ablation removes exactly one condition", test_ablation_removes_exactly_that_condition)
    run("empty disable scores everything",        test_empty_disable_scores_every_condition)
    run("default active set comes from config",   test_default_active_set_comes_from_config)
    run("thresholds track the active set",        test_thresholds_track_the_active_condition_set)
    run("CONDITION_MAX covers every condition",   test_condition_max_covers_every_scored_condition)

    print("\n── 15. analyze.py --db ──")
    run("--db targets another database",          test_analyze_db_flag_targets_another_database)
    run("--db defaults to the local database",    test_analyze_defaults_to_the_local_database)

    print("\n── 16. ForexFactory calendar timezone ──")
    run("calendar is read as UTC",                test_macro_calendar_is_read_as_utc)
    run("mixed RFC-2822 spellings all parse",     test_feed_timestamps_survive_mixed_rfc2822_spellings)
    run("junk timestamps degrade to NaT",         test_feed_timestamp_parser_survives_junk)
    run("no named timezone in the parser",        test_macro_parser_does_not_use_a_named_timezone)

    print("\n── 17. Silent-failure handlers ──")
    run("regime failure degrades loudly",         test_regime_failure_degrades_loudly)
    run("ADX failure degrades loudly",            test_adx_failure_degrades_loudly)
    run("news failure degrades loudly",           test_news_failure_degrades_loudly)

    print("\n── 18. Log output in a file ──")
    run("interrupt is a clean stop",              test_interrupt_is_a_clean_stop_not_a_traceback)
    run("progress output is plain in a file",     test_progress_output_is_plain_when_not_a_terminal)
    run("colours return on a terminal",           test_colours_return_on_a_terminal)

    print(f"\n{'══' * 20}")
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed  {'✓ ALL PASS' if FAIL == 0 else f'✗ {FAIL} FAILED'}")
    print()
    sys.exit(0 if FAIL == 0 else 1)
