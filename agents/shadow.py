"""Shadow opinions: Claude and DeepSeek judge each fired signal. Logged, never traded.

For every non-HOLD signal the bot produces, each configured provider is asked, in
parallel and under one timeout, whether it expects the trade in the signal's direction
to be profitable over the next 24h. The answer goes to `shadow_opinions` and nothing
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

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

TIMEOUT_S = 45.0
_LABELS = {"BULLISH", "BEARISH", "NEUTRAL", "BUY", "SELL", "HOLD", "WEAK", "NORMAL",
           "STRONG", "TRENDING", "RANGING", "VOLATILE", "futures", "spot"}

SYSTEM = (
    "You review trading signals from a rule-based BTC/USDT bot. You receive only "
    "numbers. Decide whether a trade in the signal's direction, entered now, is likely "
    "to be profitable over the next 24 hours after ~0.2% round-trip costs. Reply with "
    'ONLY a JSON object: {"verdict": "AGREE" or "DISAGREE", "confidence": integer '
    '0-100, "reason": "at most 25 words, in Indonesian"}.'
)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 6) if math.isfinite(f) else None


def _label(v):
    return v if isinstance(v, str) and v in _LABELS else None


def build_context(signal):
    """The prompt's facts: numbers and whitelisted labels, nothing free-text."""
    s, last = signal, signal.get("_last") or {}
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
        "price": {"close": _num(last.get("close")), "rsi": _num(last.get("rsi")),
                  "ema200_pct": pct(last.get("ema200")), "vwap_pct": pct(last.get("vwap")),
                  "atr_pct": _num(last["atr"] / e * 100) if e and _num(last.get("atr")) else None,
                  "high24_pct": pct(last.get("hi24")), "low24_pct": pct(last.get("lo24")),
                  "mfi": _num(last.get("mfi")), "cmf": _num(last.get("cmf"))},
        "htf": {k: _label(v) for k, v in (s.get("_htf") or {}).items()
                if k in ("4h", "1d", "1w") and _label(v)},
        "market": {"funding_rate_pct": g("funding", "rate_pct"), "basis_pct": g("funding", "basis_pct"),
                   "ls_ratio": g("long_short", "ratio"), "oi_change_pct": g("open_interest", "change_pct"),
                   "taker_ratio": g("taker", "ratio"), "dxy_change_pct": g("dxy", "change_pct"),
                   "sp500_change_pct": g("sp500", "change_pct"), "vix_change_pct": g("vix", "change_pct")},
        "contributions": {k: [_num(v[0]), _num(v[1])] for k, v in (s.get("_contributions") or {}).items()},
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
    return {"timestamp": datetime.now(UTC).isoformat(), "mode": signal.get("mode"),
            "signal_type": signal.get("type"), "strength": _num(signal.get("strength")),
            "threshold": _num(signal.get("_threshold")), "confidence": signal.get("confidence"),
            "entry_price": _num(signal.get("entry_price")), "signal_id": signal.get("db_id"),
            "provider": provider, "model": None, "verdict": None, "opinion_confidence": None,
            "reason": None, "error": None, "latency_ms": None,
            "input_tokens": None, "output_tokens": None}


def _one(signal, provider, prompt, ask_fn):
    rec, t0 = _base_record(signal, provider), time.time()
    try:
        reply = ask_fn(prompt, system=SYSTEM, provider=provider, max_tokens=4000)
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


def run_shadow(spot_signal, futures_signal, providers=None, ask_fn=None, log_fn=None):
    """Entry point for run_bot. Returns how many signals were put to the agents.

    Skips HOLD and cached spot replays (the 4H verdict repeated hourly, already judged
    when it was fresh).
    """
    if log_fn is None:
        from trading.history import log_shadow_opinion as log_fn
    providers = providers if providers is not None else _providers()
    if not providers:
        return 0
    n = 0
    for sig in (spot_signal, futures_signal):
        if not sig or sig.get("type") in (None, "HOLD") or sig.get("_cached"):
            continue
        n += 1
        for rec in opinions_for(sig, providers=providers, ask_fn=ask_fn):
            try:
                log_fn(rec)
            except Exception as exc:
                logger.warning("shadow opinion not stored: %s", exc)
    return n
