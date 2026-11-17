"""Flip shadow: when an opposite signal meets an open futures position, Claude says FLIP or
HOLD. Logged, never acted on.

The live bot closes the open position the moment an opposite signal clears the threshold,
and only then asks its gates whether the new one may open (`run_bot.py`, futures block).
H-FLP (`docs/superpowers/specs/2026-11-16-futures-flip-prereg.md`) tests "flip off" in a
replay; this is its forward arm. It cannot be backtested: the candles of any replay sit
inside the model's training data. Only live events are a fair test, and they are rare.

One opinion per event, asked BEFORE the flip executes, in a background thread like the
other shadows: nothing in the cycle waits for it, and a failing or slow provider costs the
cycle nothing. The answer goes to `shadow_flip_opinions` and nothing else.

`agents/shadow.py` and `agents/exit_shadow.py` are frozen for H-S and H-X; their helpers
are imported here, never edited. Scored by `scripts/flip_shadow_eval.py`.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime

from agents.exit_shadow import build_exit_context
from agents.shadow import TIMEOUT_S, _num, _providers

logger = logging.getLogger(__name__)
_threads = []

FLIP_SYSTEM = (
    "A rule-based BTC/USDT futures paper-trading bot holds ONE open position, and its "
    "engine has just produced a signal in the OPPOSITE direction. You receive only "
    "numbers. Fields named `<level>_vs_price_pct` are (level - current price) / current "
    "price x 100: negative means the level is below the current price, positive above. "
    "If you say FLIP, the bot closes the open position now at `price`, for "
    "`pnl_if_closed_now_pct`, and then opens a position in the new signal's direction if "
    "its entry gates allow it (otherwise it stays flat). If you say HOLD, the new signal "
    "is ignored and the bot keeps managing the open position by `exit_rules`: its trailing "
    "stop, its targets (half closes at the first), and a forced close at `max_hold_hours`. "
    "Choose FLIP only if you expect closing and reversing to end with a better net result "
    "than holding. Reply with ONLY a JSON object: "
    '{"verdict": "FLIP" or "HOLD", "confidence": integer 0-100, '
    '"reason": "at most 25 words, in Indonesian"}.'
)


def build_flip_context(pos, signal):
    """The exit-shadow facts for `pos` at the new signal's price, plus what the opposite
    signal proposes (its levels measured from the current price)."""
    price = signal.get("entry_price")
    ctx = build_exit_context(pos, price, signal)
    from_price = lambda lvl: _num((lvl - price) / price * 100) if price and _num(lvl) else None
    ctx["opposite_signal"] = {
        "side": signal.get("type"), "strength": _num(signal.get("strength")),
        "threshold": _num(signal.get("_threshold")), "confidence": signal.get("confidence"),
        "stop_vs_price_pct": from_price(signal.get("stop_loss")),
        "target_vs_price_pct": from_price(signal.get("take_profit"))}
    return ctx


def parse_flip_opinion(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    o = json.loads(m.group(0))
    verdict, conf, reason = o.get("verdict"), o.get("confidence"), o.get("reason")
    if verdict not in ("FLIP", "HOLD"):
        raise ValueError(f"bad verdict {verdict!r}")
    if not isinstance(conf, int) or isinstance(conf, bool) or not 0 <= conf <= 100:
        raise ValueError(f"bad confidence {conf!r}")
    return {"verdict": verdict, "confidence": conf, "reason": str(reason or "")[:300]}


def _base(pos, signal, ctx, provider):
    p = ctx["position"]
    return {"timestamp": datetime.now(UTC).isoformat(), "position_id": pos.get("id"),
            "side": pos.get("type"), "entry_price": _num(pos.get("entry_price")),
            "price": _num(signal.get("entry_price")),
            "pnl_if_closed_pct": p["pnl_if_closed_now_pct"], "hours_held": p["hours_held"],
            "new_side": signal.get("type"), "new_strength": _num(signal.get("strength")),
            "new_confidence": signal.get("confidence"),
            "new_stop_loss": _num(signal.get("stop_loss")),
            "new_take_profit": _num(signal.get("take_profit")),
            "provider": provider, "model": None, "verdict": None, "opinion_confidence": None,
            "reason": None, "error": None, "latency_ms": None,
            "input_tokens": None, "output_tokens": None}


def _one(pos, signal, ctx, provider, prompt, ask_fn):
    rec, t0 = _base(pos, signal, ctx, provider), time.time()
    try:
        reply = ask_fn(prompt, system=FLIP_SYSTEM, provider=provider, max_tokens=16000)
        rec.update(model=reply.model, input_tokens=reply.input_tokens,
                   output_tokens=reply.output_tokens)
        o = parse_flip_opinion(reply.text)
        rec.update(verdict=o["verdict"], opinion_confidence=o["confidence"], reason=o["reason"])
    except Exception as exc:          # a shadow failure is data, never an exception upward
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["latency_ms"] = int((time.time() - t0) * 1000)
    return rec


def flip_opinions_for(pos, signal, providers, ask_fn, timeout=TIMEOUT_S):
    ctx = build_flip_context(pos, signal)
    prompt = "Open position and opposite signal (JSON):\n" + json.dumps(ctx, indent=1)
    pool = ThreadPoolExecutor(max_workers=max(len(providers), 1))
    futs = {pool.submit(_one, pos, signal, ctx, p, prompt, ask_fn): p for p in providers}
    done, _ = wait(futs, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)
    out = []
    for f, p in futs.items():
        if f in done:
            out.append(f.result())
        else:
            rec = _base(pos, signal, ctx, p)
            rec.update(error="timeout", latency_ms=int(timeout * 1000))
            out.append(rec)
    return out


def run_flip_shadow(opposite_positions, signal, providers=None, ask_fn=None, log_fn=None,
                    background=False):
    """Entry point for run_bot, called where an opposite signal meets open futures
    position(s), BEFORE they are closed. Returns how many positions were put to the
    agents. Inputs are deep-copied before a background thread sees them."""
    providers = providers if providers is not None else _providers()
    if not opposite_positions or not signal or not signal.get("entry_price") or not providers:
        return 0
    if ask_fn is None:
        from agents.llm import ask as ask_fn
    jobs = [(copy.deepcopy(p), copy.deepcopy(signal)) for p in opposite_positions]

    def work():
        conn, log = None, log_fn
        if log is None:
            import sqlite3
            from trading import history
            history._conn()
            conn = sqlite3.connect(history.SIGNAL_HISTORY_DB, timeout=30)
            log = lambda rec: history.log_flip_opinion(rec, conn=conn)
        try:
            for pos, sig in jobs:
                for rec in flip_opinions_for(pos, sig, providers, ask_fn):
                    try:
                        log(rec)
                    except Exception as exc:
                        logger.warning("flip opinion not stored: %s", exc)
        finally:
            if conn is not None:
                conn.close()

    if background:
        t = threading.Thread(target=work, name="flip-shadow", daemon=False)
        t.start()
        _threads[:] = [x for x in _threads if x.is_alive()] + [t]
    else:
        work()
    return len(jobs)
