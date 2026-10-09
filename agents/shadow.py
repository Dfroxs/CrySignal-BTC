"""Shadow opinions: Claude and DeepSeek judge each fired signal. Logged, never traded.

For every non-HOLD signal the bot produces, each configured provider is asked, in
parallel and under one timeout, whether the trade AS THE BOT WILL MANAGE IT (its real
stop, targets, trailing stop, hold cap and costs, per mode; see `exit_terms`) will close
at a net profit. The answer goes to `shadow_opinions` and nothing
else: no gate reads it, no position depends on it, and a provider that fails or hangs
costs the cycle nothing.

Why this is a fair test when a backtest of an LLM is not: run 3's candles post-date
both models' training data, so neither can remember what happened next.

What the models see is NUMBERS and fixed labels only (`build_context`). Engine reasons
and news can carry outside text, and outside text in a prompt is an injection path, so
neither is sent.

Evaluated per `docs/superpowers/specs/2026-10-09-run3-prereg.md` § H-S.
Configured by `SHADOW_PROVIDERS` (default "anthropic,deepseek"; empty disables).
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# deepseek-v4-pro thinks by default: 32 s off-peak, and more than 90 s in its peak hours
# (06:08 UTC Friday, 2026-10-09). Shadow runs in a background thread (run_bot passes
# background=True), so a long cap costs the cycle nothing and stops slow hours turning
# into timeouts that would trip the prereg's 20% error discard.
TIMEOUT_S = 600.0
_threads = []             # background shadow threads, for tests and a clean shutdown
_LABELS = {"BULLISH", "BEARISH", "NEUTRAL", "BUY", "SELL", "HOLD", "WEAK", "NORMAL",
           "STRONG", "TRENDING", "RANGING", "VOLATILE", "futures", "spot"}

SYSTEM = (
    "You review trading signals from a rule-based BTC/USDT paper-trading bot. You receive "
    "only numbers. " "Fields named `<level>_vs_price_pct` are (level - current price) / current price x 100: negative means the level is below the current price, positive means above it; `*_from_entry` fields measure from the entry price the same way. "
    "The trade would be entered now at the signal's entry price and then "
    "managed exactly as described in `exit_rules`: the stop and target distances given, "
    "half closed at the first target, a trailing stop of `trailing_atr_mult` x ATR, and a "
    "forced close after `max_hold_hours`. Spot is long-only with no leverage; futures can "
    "be long or short. Decide whether this trade, managed that way, will close at a net "
    "profit after `round_trip_cost_pct`. Reply with ONLY a JSON object: "
    '{"verdict": "AGREE" or "DISAGREE", "confidence": integer 0-100, '
    '"reason": "at most 25 words, in Indonesian"}.'
)


def exit_terms(signal):
    """(signal as the bot would OPEN it, exit rules) for this signal's mode.

    Read from the same config and helpers the bot and the backtest use, never copied:
    futures stops and targets are widened at open (`apply_futures_exit_geometry`), and
    each mode has its own trailing factor, hold cap and costs.
    """
    from backtest import _costs
    from config import FUTURES_CONFIG, RISK_CONFIG
    mode = signal.get("mode", "futures")
    if mode == "futures":
        from trading.paper import apply_futures_exit_geometry
        opened = apply_futures_exit_geometry(signal)
        trail, hold = FUTURES_CONFIG["trailing_atr_factor"], RISK_CONFIG["max_position_hours"]
    else:
        opened = signal
        trail, hold = RISK_CONFIG["trailing_atr_factor"], RISK_CONFIG["max_position_hours_spot"]
    return opened, {"mode": mode, "timeframe": "1h" if mode == "futures" else "4h",
                    "long_only": mode == "spot", "max_hold_hours": hold,
                    "trailing_atr_mult": trail, "partial_close_at_first_target_pct": 50,
                    "round_trip_cost_pct": round(_costs(mode, 2), 4)}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if math.isfinite(f) else None


def _label(v):
    return v if isinstance(v, str) and v in _LABELS else None


def build_context(signal):
    """The prompt's facts: numbers and whitelisted labels, nothing free-text. Stop and
    target are the levels the bot would actually open with."""
    s, rules = exit_terms(signal)
    last = s.get("_last") or {}
    e = _num(s.get("entry_price")) or 0.0
    pct = lambda p: _num((p - e) / e * 100) if e and _num(p) is not None else None
    m = s.get("_market") or {}
    g = lambda k, f: _num((m.get(k) or {}).get(f))
    thr = _num(s.get("_threshold"))
    return {
        "time_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:00"),
        "signal": {"type": _label(s.get("type")), "mode": _label(s.get("mode")),
                   "strength": _num(s.get("strength")), "threshold": thr,
                   "strength_over_threshold": _num(s["strength"] / thr) if thr else None,
                   "confidence": _label(s.get("confidence")),
                   "buy_score": _num(s.get("buy_score")), "sell_score": _num(s.get("sell_score")),
                   "stop_pct_from_entry": pct(s.get("stop_loss")),
                   "target_pct_from_entry": pct(s.get("take_profit"))},
        # `<level>_vs_price_pct` = (level − price)/price: a live probe read the old
        # `ema200_pct` backwards ("price below EMA200" with price 2% above it).
        "price": {"close": _num(last.get("close")), "rsi": _num(last.get("rsi")),
                  "ema200_vs_price_pct": pct(last.get("ema200")),
                  "price_above_ema200": (e > last["ema200"]) if e and _num(last.get("ema200")) else None,
                  "vwap_vs_price_pct": pct(last.get("vwap")),
                  "price_above_vwap": (e > last["vwap"]) if e and _num(last.get("vwap")) else None,
                  "atr_pct": _num(last["atr"] / e * 100) if e and _num(last.get("atr")) else None,
                  "high24_vs_price_pct": pct(last.get("hi24")), "low24_vs_price_pct": pct(last.get("lo24")),
                  "mfi": _num(last.get("mfi")), "cmf": _num(last.get("cmf"))},
        "htf": {k: _label(v) for k, v in (s.get("_htf") or {}).items()
                if k in ("4h", "1d", "1w") and _label(v)},
        "market": {"funding_rate_pct": g("funding", "rate_pct"), "basis_pct": g("funding", "basis_pct"),
                   "ls_ratio": g("long_short", "ratio"), "oi_change_pct": g("open_interest", "change_pct"),
                   "taker_ratio": g("taker", "ratio"), "dxy_change_pct": g("dxy", "change_pct"),
                   "sp500_change_pct": g("sp500", "change_pct"), "vix_change_pct": g("vix", "change_pct")},
        "contributions": {k: [_num(v[0]), _num(v[1])] for k, v in (s.get("_contributions") or {}).items()},
        "exit_rules": rules,
    }


def parse_opinion(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    o = json.loads(m.group(0))
    verdict, conf, reason = o.get("verdict"), o.get("confidence"), o.get("reason")
    if verdict not in ("AGREE", "DISAGREE"):
        raise ValueError(f"bad verdict {verdict!r}")
    if not isinstance(conf, int) or isinstance(conf, bool) or not 0 <= conf <= 100:
        raise ValueError(f"bad confidence {conf!r}")
    return {"verdict": verdict, "confidence": conf, "reason": str(reason or "")[:300]}


def _providers():
    raw = os.getenv("SHADOW_PROVIDERS", "anthropic,deepseek")
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _base_record(signal, provider):
    opened, _ = exit_terms(signal)
    atr = signal.get("atr") or (signal.get("_last") or {}).get("atr")
    return {"timestamp": datetime.now(UTC).isoformat(), "mode": signal.get("mode"),
            "stop_loss": _num(opened.get("stop_loss")), "take_profit": _num(opened.get("take_profit")),
            "tp2": _num(opened.get("tp2")), "atr": _num(atr),
            "signal_type": signal.get("type"), "strength": _num(signal.get("strength")),
            "threshold": _num(signal.get("_threshold")), "confidence": signal.get("confidence"),
            "entry_price": _num(signal.get("entry_price")), "signal_id": signal.get("db_id"),
            "provider": provider, "model": None, "verdict": None, "opinion_confidence": None,
            "reason": None, "error": None, "latency_ms": None,
            "input_tokens": None, "output_tokens": None}


def _one(signal, provider, prompt, ask_fn):
    rec, t0 = _base_record(signal, provider), time.time()
    try:
        # Thinking tokens count against max_tokens: deepseek-v4-pro spent a 4,000 budget
        # thinking and returned nothing (2026-10-09). Only tokens used are billed.
        reply = ask_fn(prompt, system=SYSTEM, provider=provider, max_tokens=16000)
        rec.update(model=reply.model, input_tokens=reply.input_tokens,
                   output_tokens=reply.output_tokens)
        o = parse_opinion(reply.text)
        rec.update(verdict=o["verdict"], opinion_confidence=o["confidence"], reason=o["reason"])
    except Exception as exc:          # a shadow failure is data, never an exception upward
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["latency_ms"] = int((time.time() - t0) * 1000)
    return rec


def opinions_for(signal, providers=None, ask_fn=None, timeout=TIMEOUT_S):
    """One record per provider. A provider still running at `timeout` is recorded as
    'timeout' and abandoned; the caller does not wait for it."""
    if ask_fn is None:
        from agents.llm import ask as ask_fn
    providers = providers if providers is not None else _providers()
    prompt = "Signal facts (JSON):\n" + json.dumps(build_context(signal), indent=1)
    pool = ThreadPoolExecutor(max_workers=max(len(providers), 1))
    futs = {pool.submit(_one, signal, p, prompt, ask_fn): p for p in providers}
    done, _ = wait(futs, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)
    out = []
    for f, p in futs.items():
        if f in done:
            out.append(f.result())
        else:
            rec = _base_record(signal, p)
            rec.update(error="timeout", latency_ms=int(timeout * 1000))
            out.append(rec)
    return out


def run_shadow(spot_signal, futures_signal, providers=None, ask_fn=None, log_fn=None,
               background=False):
    """Entry point for run_bot. Returns how many signals were put to the agents.

    Skips HOLD and cached spot replays (the 4H verdict repeated hourly, already judged
    when it was fresh). With `background=True` the work runs in its own non-daemon
    thread and this returns at once. The signals are deep-copied first, so the cycle may
    go on using its own, and the thread writes through its own SQLite connection rather
    than sharing the bot's.
    """
    providers = providers if providers is not None else _providers()
    sigs = [copy.deepcopy(s) for s in (spot_signal, futures_signal)
            if s and s.get("type") not in (None, "HOLD") and not s.get("_cached")]
    if not providers or not sigs:
        return 0

    def work():
        conn, log = None, log_fn
        if log is None:
            import sqlite3
            from trading import history
            history._conn()           # make sure the schema (and migrations) exist first
            conn = sqlite3.connect(history.SIGNAL_HISTORY_DB, timeout=30)
            log = lambda rec: history.log_shadow_opinion(rec, conn=conn)
        try:
            for sig in sigs:
                for rec in opinions_for(sig, providers=providers, ask_fn=ask_fn):
                    try:
                        log(rec)
                    except Exception as exc:
                        logger.warning("shadow opinion not stored: %s", exc)
        finally:
            if conn is not None:
                conn.close()

    if background:
        t = threading.Thread(target=work, name="shadow-agents", daemon=False)
        t.start()
        _threads[:] = [x for x in _threads if x.is_alive()] + [t]
    else:
        work()
    return len(sigs)
